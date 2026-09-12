# 掛號提醒生命週期：後端實作報告（回覆前端 2026-09-12 需求文件）

日期：2026-09-12
狀態：已實作、單元測試全綠（見 §0），**尚未部署**
對應 openspec：`openspec/changes/appointment-reminders/`（spec 已依本次行為更新，tasks 新增第 7 節）
前一份報告：`docs/2026-09-10-appointment-reminders-backend-report.md`

---

## 0. 先看這裡

### 需要產品／前端知道的事

1. **影子模式家庭的實際影響比字面大。** 「只有讀取權的家人不能更動掛號」與「家屬推播只送 GUARDIAN、CAREGIVER」兩條拍板我都照做了，但要知道它們在**尚未指派角色**的家庭會怎麼落地：族譜成員沒有 `family_role` 時一律解析為 MEMBER（`DEFAULT_FAMILY_ROLE`）。所以上線之後，**沒有做過角色指派的家庭，只有長輩本人能新增、修改、取消、刪除掛號，而且家人收不到任何一則掛號推播**——在這之前，影子模式下全家都收得到、也都改得動。這不是 bug，是拍板的直接結果；我沒有停下來，因為規則明確、也做得到。但影響面取決於目前有多少家庭完成了角色指派，程式碼裡看不到這個數字，建議上線前查一下 `family_trees` 裡 `family_role` 缺席的比例。
2. **上線順序。** 後端的嚴格判定、`my_strict_permissions`、`scope` 會一起上。前端必須在同一版改用 `my_strict_permissions.general` 判斷掛號的寫入按鈕，否則影子模式下的 MEMBER 會看到按了必定 403 的按鈕（需求五「連帶二」的描述是對的）。
3. **「刪除全部歷史紀錄」是單一 `delete_many`，不是交易。** 失敗時的行為見 Q2。

### 改了哪些檔案

| 檔案 | 改了什麼 |
|---|---|
| `app/models/appointment.py` | 新增 `cancelled_at`、`cancelled_by_user_id`；新增 `AppointmentListScope`、`AppointmentReminderPage` |
| `app/repositories/appointment_repository.py` | `past_filter`（「過去」的唯一定義）與 `scope_filter`；`list_scope`、`count_scope`、`delete_past`、`mark_cancelled`；出發／到診加上當日結束條件；`update_fields` 可條件式寫入 |
| `app/services/appointment/appointment_service.py` | `cancel`、`list_scope`、`delete_past`；重複規則改成「同一瞬間」；改時間改為條件式寫入、已取消不能改時間；寫入授權改嚴格 |
| `app/routers/users/appointments.py` | `POST .../cancel`、`DELETE /reminders`、`GET` 的 `scope`／`limit`／`cursor`；寫入授權改嚴格 |
| `app/models/family_authorization.py` | 登記兩個新欄位；新增 `STRICT_NOTIFICATION_KINDS` |
| `app/services/family/family_authorization_service.py` | `appointment_reminder` 推播在影子模式下也依政策篩選；`describe_members` 附 `my_strict_permissions` |
| `app/models/family_tree.py`、`app/routers/users/family_tree.py` | `GET /api/family/me` 的成員新增 `my_strict_permissions` |
| `openspec/changes/appointment-reminders/` | spec 更新、tasks 第 7 節 |
| `tests/unit/appointment/`（6 個檔）、`tests/unit/services/family/test_family_authorization_service.py`、`tests/unit/routers/test_endpoint_authorization.py` | 見 §8 |

刪掉的：`AppointmentService` 裡比對院所與科別的 `_is_same_appointment`、`_norm_key`（需求四之後沒有用途）；router 測試裡自己手寫的族譜替身（改用 `tests/unit/appointment/support.py` 的 `real_authz`，排程器、postback、router 三處共用）。

### 測試

- `venv/Scripts/python.exe -m pytest tests/`：**4136 passed、1 failed**。唯一的失敗是 `test_require_magick_passes_when_binary_is_present`，需要本機安裝 ImageMagick，與本次無關（前一份報告已記錄，在沒有改動的 HEAD 上同樣失敗）。
- 掛號與家庭授權相關的測試：改動前 672 passed，改動後 760 passed。
- 本報告 §2、§4 的 JSON 全部是測試用 client 打**真實 app** 錄下來的：真的 router、真的服務層、真的 `FamilyAuthorizationService`（族譜設為**影子模式**，也就是目前的預設），只把資料庫換成記憶體。沒有手寫的範例。錄製過程抓到一個問題並已修正：已取消的未來門診按「我已出發」原本回「門診當天才能回報出發或到診。」，現在回「這筆掛號提醒已經取消，無法回報出發或到診。」。

---

## 1. 新增／變更的 API

**認證**：全部沿用 `Authorization: Bearer <JWT>`（`POST /api/auth/liff/login` 簽發）。

| # | 方法 | 完整路徑 | Query | Body | 成功回應 | 授權 | 本次 |
|---|---|---|---|---|---|---|---|
| 1 | GET | `/api/appointments/reminders` | `target_user_id`（選填）<br>`scope`：`upcoming`／`past`（選填）<br>`limit`：1–50，預設 20（只對 past）<br>`cursor`（只對 past）<br>`include_past`（只在不帶 scope 時有效） | — | 帶 `scope`：`200` 物件 `{items, next_cursor, total_count}`<br>不帶：`200` 陣列（舊格式） | 本人無條件；查別人需 `GENERAL READ`（不變） | **新增參數** |
| 2 | DELETE | `/api/appointments/reminders` | `scope`（**必填**，只能是 `past`）<br>`target_user_id`（選填，只能省略或等於本人） | — | `200` `{"deleted": N}` | **只有本人**，不經家庭授權 | **新增** |
| 3 | POST | `/api/appointments/reminders/{reminder_id}/cancel` | — | 不需要（帶了也忽略） | `200` 單筆 | 本人，或對就診者有 `GENERAL WRITE`（嚴格判定） | **新增** |
| 4 | POST | `/api/appointments/reminders` | — | 同前 | `200` 單筆 | 為別人建立：`GENERAL WRITE`（**改嚴格**） | 授權、重複規則 |
| 5 | PUT | `/api/appointments/reminders/{reminder_id}` | — | 同前 | `200` 單筆 | `GENERAL WRITE`（**改嚴格**） | 授權、已取消不能改時間、條件式寫入、重複規則 |
| 6 | DELETE | `/api/appointments/reminders/{reminder_id}` | — | — | `200` `{"ok": true}` | `GENERAL WRITE`（**改嚴格**） | 授權 |
| 7 | POST | `/api/appointments/reminders/{reminder_id}/depart` | — | 不需要 | `200` 單筆 | 同 cancel（**改嚴格**） | 授權、當日結束條件 |
| 8 | POST | `/api/appointments/reminders/{reminder_id}/attend` | — | 不需要 | `200` 單筆 | 同 cancel（**改嚴格**） | 授權、當日結束條件 |
| 9 | GET | `/api/family/me` | — | — | `200` | 已登入 | 成員多一個 `my_strict_permissions` |

所有回傳掛號提醒的端點（1、3、4、5、7、8）的每一筆都多兩個 key：`cancelled_at`、`cancelled_by_user_id`（§3）。LINE 卡片上的「我已出發／我已到診」與 7、8 走同一個服務方法，授權與條件完全相同。

### `GET /reminders` 的參數細節

| 參數 | 說明 |
|---|---|
| `scope=upcoming` | 沒有取消、而且當日還沒結束的門診。依 `appointment_at` **由早到晚**，**不分頁**，回全部；`next_cursor` 一律 `null`，`total_count` 等於 `items` 的筆數。帶了 `limit`／`cursor` 會被忽略 |
| `scope=past` | 當日已結束，**或已經取消**的門診。依 `appointment_at` **由新到舊**，同一時間多筆時依 id 由新到舊。cursor 分頁 |
| `limit` | 1–50，預設 20。超出範圍是 FastAPI 的 422（§4） |
| `cursor` | 上一頁的 `next_cursor`，**原樣帶回**，不要解析。竄改或格式不對 → 400 |
| 不帶 `scope` | 與前一份報告完全相同：陣列、由早到晚，`include_past=false` 只回「當日結束還沒到」的。**注意**：這條舊路徑的界線只看當日結束，所以取消了的未來門診會出現在 `include_past=false` 的清單裡——判定沒有變，只是 `cancelled` 以前不會出現 |

「當日結束」沿用前一份報告 §7：當地午夜，但不早於門診後 60 分鐘，與排程器標記 `missed` 同一條界線（`day_end_at`）。

---

## 2. 請求／回應實際 JSON

角色：`U4af49…`＝長輩本人，`Ub2c7e…`＝女兒（CAREGIVER），`U9d8c7…`＝兒子（MEMBER，只有讀取權）。族譜處於影子模式。資料庫裡預先有三筆舊門診：8/25 眼科（已到診）、9/01 心臟內科（已到診）、9/08 家醫科（未到診，`missed`）。

### 2.1 `POST /reminders`（本人建立）——新欄位在建立時是 null

伺服器時間 `2026-09-14T10:00:00+08:00`，呼叫者＝長輩本人。

```http
POST /api/appointments/reminders
```

Request body：

```json
{
  "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "appointment_at": "2026-09-15T09:30:00+08:00",
  "facility_id": "abc123",
  "hospital_name": "台大醫院",
  "hospital_address": "臺北市中正區中山南路7號",
  "hospital_phone": "0223123456",
  "department": "心臟內科",
  "doctor_name": "王大明",
  "serial_number": "23",
  "note": null
}
```

Response `200`：

```json
{
  "id": "6aa4f0b0922e51d637c5b278",
  "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "creator_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "appointment_at": "2026-09-15T09:30:00+08:00",
  "facility_id": "abc123",
  "hospital_name": "台大醫院",
  "hospital_address": "臺北市中正區中山南路7號",
  "hospital_phone": "0223123456",
  "department": "心臟內科",
  "doctor_name": "王大明",
  "serial_number": "23",
  "note": null,
  "status": "scheduled",
  "departed_at": null,
  "departed_by_user_id": null,
  "attended_at": null,
  "attended_by_user_id": null,
  "cancelled_at": null,
  "cancelled_by_user_id": null,
  "enabled": true,
  "notify_at": [
    "2026-09-15T08:30:00+08:00",
    "2026-09-15T09:30:00+08:00",
    "2026-09-15T10:00:00+08:00"
  ],
  "created_at": "2026-09-14T10:00:00+08:00",
  "updated_at": "2026-09-14T10:00:00+08:00"
}
```

同一時間（9/15 14:00）另一筆「馬偕紀念醫院 骨科」、以及女兒代建的 9/18 14:10 仁愛診所也照此建立（id 見下方）。

### 2.2 `POST .../cancel`（取消後的單筆）

伺服器時間 `2026-09-14T10:00:00+08:00`，呼叫者＝女兒（CAREGIVER）。

```http
POST /api/appointments/reminders/6aa4f0b0922e51d637c5b279/cancel
```

Response `200`：

```json
{
  "id": "6aa4f0b0922e51d637c5b279",
  "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "creator_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
  "appointment_at": "2026-09-18T14:10:00+08:00",
  "facility_id": null,
  "hospital_name": "仁愛診所",
  "hospital_address": null,
  "hospital_phone": null,
  "department": "家庭醫學科",
  "doctor_name": null,
  "serial_number": null,
  "note": null,
  "status": "cancelled",
  "departed_at": null,
  "departed_by_user_id": null,
  "attended_at": null,
  "attended_by_user_id": null,
  "cancelled_at": "2026-09-14T10:00:00+08:00",
  "cancelled_by_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
  "enabled": true,
  "notify_at": [],
  "created_at": "2026-09-14T10:00:00+08:00",
  "updated_at": "2026-09-14T10:00:00+08:00"
}
```

- `status` 變成 `cancelled`，`notify_at` 是空陣列：本人與家屬尚未發出的 T-1h、T+0、T+30 全部不會再發。
- `cancelled_at` 帶 `appointment_at` 的 offset。`updated_at` 同步更新。
- 不發任何「已取消」通知。

### 2.3 `POST .../cancel` 第二次（冪等）

伺服器時間 `2026-09-14T10:05:00+08:00`，呼叫者＝長輩本人。

Response `200`：

```json
{
  "id": "6aa4f0b0922e51d637c5b279",
  "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "creator_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
  "appointment_at": "2026-09-18T14:10:00+08:00",
  "facility_id": null,
  "hospital_name": "仁愛診所",
  "hospital_address": null,
  "hospital_phone": null,
  "department": "家庭醫學科",
  "doctor_name": null,
  "serial_number": null,
  "note": null,
  "status": "cancelled",
  "departed_at": null,
  "departed_by_user_id": null,
  "attended_at": null,
  "attended_by_user_id": null,
  "cancelled_at": "2026-09-14T10:00:00+08:00",
  "cancelled_by_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
  "enabled": true,
  "notify_at": [],
  "created_at": "2026-09-14T10:00:00+08:00",
  "updated_at": "2026-09-14T10:00:00+08:00"
}
```

與 2.2 **逐字相同**：`cancelled_by_user_id` 仍是女兒，`cancelled_at`、`updated_at` 仍是第一次的時間。

### 2.4 取消之後在原時間重掛

伺服器時間 `2026-09-14T10:05:00+08:00`，呼叫者＝長輩本人。

```http
POST /api/appointments/reminders
```

Request body：

```json
{
  "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "appointment_at": "2026-09-18T14:10:00+08:00",
  "facility_id": null,
  "hospital_name": "仁愛診所",
  "hospital_address": null,
  "hospital_phone": null,
  "department": "家庭醫學科",
  "doctor_name": null,
  "serial_number": null,
  "note": null
}
```

Response `200`：

```json
{
  "id": "6aa4f0b0922e51d637c5b27b",
  "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "creator_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "appointment_at": "2026-09-18T14:10:00+08:00",
  "facility_id": null,
  "hospital_name": "仁愛診所",
  "hospital_address": null,
  "hospital_phone": null,
  "department": "家庭醫學科",
  "doctor_name": null,
  "serial_number": null,
  "note": null,
  "status": "scheduled",
  "departed_at": null,
  "departed_by_user_id": null,
  "attended_at": null,
  "attended_by_user_id": null,
  "cancelled_at": null,
  "cancelled_by_user_id": null,
  "enabled": true,
  "notify_at": [
    "2026-09-18T13:10:00+08:00",
    "2026-09-18T14:10:00+08:00",
    "2026-09-18T14:40:00+08:00"
  ],
  "created_at": "2026-09-14T10:05:00+08:00",
  "updated_at": "2026-09-14T10:05:00+08:00"
}
```

已取消的那一筆不算撞時段（需求四與需求一的交互）。

### 2.5 `GET /reminders?scope=upcoming`

伺服器時間 `2026-09-14T10:05:00+08:00`，呼叫者＝長輩本人。

```http
GET /api/appointments/reminders?scope=upcoming
```

Response `200`：

```json
{
  "items": [
    {
      "id": "6aa4f0b0922e51d637c5b278",
      "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "creator_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "appointment_at": "2026-09-15T09:30:00+08:00",
      "facility_id": "abc123",
      "hospital_name": "台大醫院",
      "hospital_address": "臺北市中正區中山南路7號",
      "hospital_phone": "0223123456",
      "department": "心臟內科",
      "doctor_name": "王大明",
      "serial_number": "23",
      "note": null,
      "status": "scheduled",
      "departed_at": null,
      "departed_by_user_id": null,
      "attended_at": null,
      "attended_by_user_id": null,
      "cancelled_at": null,
      "cancelled_by_user_id": null,
      "enabled": true,
      "notify_at": [
        "2026-09-15T08:30:00+08:00",
        "2026-09-15T09:30:00+08:00",
        "2026-09-15T10:00:00+08:00"
      ],
      "created_at": "2026-09-14T10:00:00+08:00",
      "updated_at": "2026-09-14T10:00:00+08:00"
    },
    {
      "id": "6aa4f0b0922e51d637c5b27a",
      "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "creator_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "appointment_at": "2026-09-15T14:00:00+08:00",
      "facility_id": null,
      "hospital_name": "馬偕紀念醫院",
      "hospital_address": null,
      "hospital_phone": null,
      "department": "骨科",
      "doctor_name": null,
      "serial_number": null,
      "note": null,
      "status": "scheduled",
      "departed_at": null,
      "departed_by_user_id": null,
      "attended_at": null,
      "attended_by_user_id": null,
      "cancelled_at": null,
      "cancelled_by_user_id": null,
      "enabled": true,
      "notify_at": [
        "2026-09-15T13:00:00+08:00",
        "2026-09-15T14:00:00+08:00",
        "2026-09-15T14:30:00+08:00"
      ],
      "created_at": "2026-09-14T10:00:00+08:00",
      "updated_at": "2026-09-14T10:00:00+08:00"
    },
    {
      "id": "6aa4f0b0922e51d637c5b27b",
      "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "creator_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "appointment_at": "2026-09-18T14:10:00+08:00",
      "facility_id": null,
      "hospital_name": "仁愛診所",
      "hospital_address": null,
      "hospital_phone": null,
      "department": "家庭醫學科",
      "doctor_name": null,
      "serial_number": null,
      "note": null,
      "status": "scheduled",
      "departed_at": null,
      "departed_by_user_id": null,
      "attended_at": null,
      "attended_by_user_id": null,
      "cancelled_at": null,
      "cancelled_by_user_id": null,
      "enabled": true,
      "notify_at": [
        "2026-09-18T13:10:00+08:00",
        "2026-09-18T14:10:00+08:00",
        "2026-09-18T14:40:00+08:00"
      ],
      "created_at": "2026-09-14T10:05:00+08:00",
      "updated_at": "2026-09-14T10:05:00+08:00"
    }
  ],
  "next_cursor": null,
  "total_count": 3
}
```

9/18 那筆已取消的不在這裡（它在 past），重掛的新那筆在。

### 2.6 `GET /reminders?scope=past&limit=2`（分頁第一頁）

伺服器時間 `2026-09-14T10:05:00+08:00`，呼叫者＝長輩本人。

```http
GET /api/appointments/reminders?scope=past&limit=2
```

Response `200`：

```json
{
  "items": [
    {
      "id": "6aa4f0b0922e51d637c5b279",
      "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "creator_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
      "appointment_at": "2026-09-18T14:10:00+08:00",
      "facility_id": null,
      "hospital_name": "仁愛診所",
      "hospital_address": null,
      "hospital_phone": null,
      "department": "家庭醫學科",
      "doctor_name": null,
      "serial_number": null,
      "note": null,
      "status": "cancelled",
      "departed_at": null,
      "departed_by_user_id": null,
      "attended_at": null,
      "attended_by_user_id": null,
      "cancelled_at": "2026-09-14T10:00:00+08:00",
      "cancelled_by_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
      "enabled": true,
      "notify_at": [],
      "created_at": "2026-09-14T10:00:00+08:00",
      "updated_at": "2026-09-14T10:00:00+08:00"
    },
    {
      "id": "6aa4f0af922e51d637c5b276",
      "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "creator_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "appointment_at": "2026-09-08T14:10:00+08:00",
      "facility_id": null,
      "hospital_name": "仁愛診所",
      "hospital_address": null,
      "hospital_phone": null,
      "department": "家庭醫學科",
      "doctor_name": null,
      "serial_number": null,
      "note": null,
      "status": "missed",
      "departed_at": null,
      "departed_by_user_id": null,
      "attended_at": null,
      "attended_by_user_id": null,
      "cancelled_at": null,
      "cancelled_by_user_id": null,
      "enabled": true,
      "notify_at": [],
      "created_at": "2026-09-05T14:10:00+08:00",
      "updated_at": "2026-09-05T14:10:00+08:00"
    }
  ],
  "next_cursor": "eyJhdCI6IjIwMjYtMDktMDhUMDY6MTA6MDArMDA6MDAiLCJpZCI6IjZhYTRmMGFmOTIyZTUxZDYzN2M1YjI3NiJ9",
  "total_count": 4
}
```

- 下個月才要看的 9/18 因為已取消，排在過去的最前面（由新到舊）。
- `total_count` 是整個 past 的 4 筆，不是這一頁的 2 筆。

### 2.7 第二頁（帶上一頁的 `next_cursor`）

```http
GET /api/appointments/reminders?scope=past&limit=2&cursor=eyJhdCI6IjIwMjYtMDktMDhUMDY6MTA6MDArMDA6MDAiLCJpZCI6IjZhYTRmMGFmOTIyZTUxZDYzN2M1YjI3NiJ9
```

Response `200`：

```json
{
  "items": [
    {
      "id": "6aa4f0af922e51d637c5b275",
      "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "creator_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "appointment_at": "2026-09-01T10:00:00+08:00",
      "facility_id": "abc123",
      "hospital_name": "台大醫院",
      "hospital_address": null,
      "hospital_phone": null,
      "department": "心臟內科",
      "doctor_name": null,
      "serial_number": null,
      "note": null,
      "status": "attended",
      "departed_at": null,
      "departed_by_user_id": null,
      "attended_at": "2026-09-01T09:52:00+08:00",
      "attended_by_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "cancelled_at": null,
      "cancelled_by_user_id": null,
      "enabled": true,
      "notify_at": [],
      "created_at": "2026-08-29T10:00:00+08:00",
      "updated_at": "2026-08-29T10:00:00+08:00"
    },
    {
      "id": "6aa4f0af922e51d637c5b277",
      "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "creator_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "appointment_at": "2026-08-25T09:00:00+08:00",
      "facility_id": null,
      "hospital_name": "馬偕紀念醫院",
      "hospital_address": null,
      "hospital_phone": null,
      "department": "眼科",
      "doctor_name": null,
      "serial_number": null,
      "note": null,
      "status": "attended",
      "departed_at": null,
      "departed_by_user_id": null,
      "attended_at": "2026-08-25T09:20:00+08:00",
      "attended_by_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
      "cancelled_at": null,
      "cancelled_by_user_id": null,
      "enabled": true,
      "notify_at": [],
      "created_at": "2026-08-22T09:00:00+08:00",
      "updated_at": "2026-08-22T09:00:00+08:00"
    }
  ],
  "next_cursor": null,
  "total_count": 4
}
```

`next_cursor: null`：已經翻到最舊的一筆。

### 2.8 家屬讀長輩的 past（MEMBER 讀取不受嚴格判定影響）

伺服器時間 `2026-09-14T10:05:00+08:00`，呼叫者＝兒子（MEMBER）。

```http
GET /api/appointments/reminders?target_user_id=U4af4980629d8f1f1e6bd8e0f7c4c5a1b&scope=past&limit=1
```

Response `200`：

```json
{
  "items": [
    {
      "id": "6aa4f0b0922e51d637c5b279",
      "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "creator_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
      "appointment_at": "2026-09-18T14:10:00+08:00",
      "facility_id": null,
      "hospital_name": "仁愛診所",
      "hospital_address": null,
      "hospital_phone": null,
      "department": "家庭醫學科",
      "doctor_name": null,
      "serial_number": null,
      "note": null,
      "status": "cancelled",
      "departed_at": null,
      "departed_by_user_id": null,
      "attended_at": null,
      "attended_by_user_id": null,
      "cancelled_at": "2026-09-14T10:00:00+08:00",
      "cancelled_by_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
      "enabled": true,
      "notify_at": [],
      "created_at": "2026-09-14T10:00:00+08:00",
      "updated_at": "2026-09-14T10:00:00+08:00"
    }
  ],
  "next_cursor": "eyJhdCI6IjIwMjYtMDktMThUMDY6MTA6MDArMDA6MDAiLCJpZCI6IjZhYTRmMGIwOTIyZTUxZDYzN2M1YjI3OSJ9",
  "total_count": 4
}
```

### 2.9 不帶 `scope`（舊格式，未變）

```http
GET /api/appointments/reminders?include_past=true
```

回傳陣列，形狀與前一份報告相同，只多了兩個新 key。7 筆裡取消的那一筆長這樣：

```json
{
  "id": "6aa4f0b0922e51d637c5b279",
  "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "creator_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
  "appointment_at": "2026-09-18T14:10:00+08:00",
  "facility_id": null,
  "hospital_name": "仁愛診所",
  "hospital_address": null,
  "hospital_phone": null,
  "department": "家庭醫學科",
  "doctor_name": null,
  "serial_number": null,
  "note": null,
  "status": "cancelled",
  "departed_at": null,
  "departed_by_user_id": null,
  "attended_at": null,
  "attended_by_user_id": null,
  "cancelled_at": "2026-09-14T10:00:00+08:00",
  "cancelled_by_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
  "enabled": true,
  "notify_at": [],
  "created_at": "2026-09-14T10:00:00+08:00",
  "updated_at": "2026-09-14T10:00:00+08:00"
}
```

### 2.10 出發／到診（新欄位為 null）

伺服器時間 `2026-09-15T08:40:00+08:00`，呼叫者＝女兒（CAREGIVER）。

Response `200`：

```json
{
  "id": "6aa4f0b0922e51d637c5b278",
  "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "creator_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "appointment_at": "2026-09-15T09:30:00+08:00",
  "facility_id": "abc123",
  "hospital_name": "台大醫院",
  "hospital_address": "臺北市中正區中山南路7號",
  "hospital_phone": "0223123456",
  "department": "心臟內科",
  "doctor_name": "王大明",
  "serial_number": "23",
  "note": null,
  "status": "departed",
  "departed_at": "2026-09-15T08:40:00+08:00",
  "departed_by_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
  "attended_at": null,
  "attended_by_user_id": null,
  "cancelled_at": null,
  "cancelled_by_user_id": null,
  "enabled": true,
  "notify_at": [
    "2026-09-15T08:30:00+08:00",
    "2026-09-15T09:30:00+08:00",
    "2026-09-15T10:00:00+08:00"
  ],
  "created_at": "2026-09-14T10:00:00+08:00",
  "updated_at": "2026-09-15T08:40:00+08:00"
}
```

伺服器時間 `2026-09-15T09:35:00+08:00`，呼叫者＝長輩本人。

Response `200`：

```json
{
  "id": "6aa4f0b0922e51d637c5b278",
  "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "creator_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "appointment_at": "2026-09-15T09:30:00+08:00",
  "facility_id": "abc123",
  "hospital_name": "台大醫院",
  "hospital_address": "臺北市中正區中山南路7號",
  "hospital_phone": "0223123456",
  "department": "心臟內科",
  "doctor_name": "王大明",
  "serial_number": "23",
  "note": null,
  "status": "attended",
  "departed_at": "2026-09-15T08:40:00+08:00",
  "departed_by_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
  "attended_at": "2026-09-15T09:35:00+08:00",
  "attended_by_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "cancelled_at": null,
  "cancelled_by_user_id": null,
  "enabled": true,
  "notify_at": [],
  "created_at": "2026-09-14T10:00:00+08:00",
  "updated_at": "2026-09-15T09:35:00+08:00"
}
```

### 2.11 `DELETE /reminders?scope=past`（刪除全部歷史紀錄）

伺服器時間 `2026-09-16T00:05:00+08:00`。此時 9/15 的兩筆門診當日都已結束（其中 14:00 那筆還沒被排程器標記 `missed`，仍算過去），加上 8/25、9/01、9/08 與取消的 9/18，past 共 6 筆：

```http
GET /api/appointments/reminders?scope=past&limit=1
```

`total_count` 為 `6`。按下刪除全部：

```http
DELETE /api/appointments/reminders?scope=past
```

Response `200`：

```json
{
  "deleted": 6
}
```

與畫面上的 `total_count` 一致（兩者用同一份判定）。再按一次（帶自己的 id，等同省略）：

```http
DELETE /api/appointments/reminders?scope=past&target_user_id=U4af4980629d8f1f1e6bd8e0f7c4c5a1b
```

Response `200`：

```json
{
  "deleted": 0
}
```

即將到來的那一筆不受影響：

Response `200`：

```json
{
  "items": [
    {
      "id": "6aa4f0b0922e51d637c5b27b",
      "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "creator_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
      "appointment_at": "2026-09-18T14:10:00+08:00",
      "facility_id": null,
      "hospital_name": "仁愛診所",
      "hospital_address": null,
      "hospital_phone": null,
      "department": "家庭醫學科",
      "doctor_name": null,
      "serial_number": null,
      "note": null,
      "status": "scheduled",
      "departed_at": null,
      "departed_by_user_id": null,
      "attended_at": null,
      "attended_by_user_id": null,
      "cancelled_at": null,
      "cancelled_by_user_id": null,
      "enabled": true,
      "notify_at": [
        "2026-09-18T13:10:00+08:00",
        "2026-09-18T14:10:00+08:00",
        "2026-09-18T14:40:00+08:00"
      ],
      "created_at": "2026-09-14T10:05:00+08:00",
      "updated_at": "2026-09-14T10:05:00+08:00"
    }
  ],
  "next_cursor": null,
  "total_count": 1
}
```

### 2.12 `GET /api/family/me`：`my_strict_permissions`

呼叫者＝兒子。他對長輩是 MEMBER；對爺爺（`U1a2b3c…`）也是 MEMBER，但**持有有效委任**。兩位的族譜都是影子模式。

Response `200`：

```json
{
  "family_tree": {
    "user_id": "U9d8c7b6a5f4e3d2c1b0a9f8e7d6c5b4a",
    "family_members": [
      {
        "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
        "relationship_type": null,
        "display_name": null,
        "picture_url": null,
        "is_care_recipient": false,
        "family_role": "GUARDIAN",
        "my_role": "MEMBER",
        "my_permissions": {
          "general": [
            "READ",
            "WRITE"
          ],
          "sensitive": [
            "READ"
          ],
          "private": [
            "READ"
          ]
        },
        "my_strict_permissions": {
          "general": [
            "READ"
          ],
          "sensitive": [],
          "private": []
        },
        "rbac_migration_state": "shadow"
      },
      {
        "user_id": "U1a2b3c4d5e6f708192a3b4c5d6e7f809",
        "relationship_type": null,
        "display_name": null,
        "picture_url": null,
        "is_care_recipient": false,
        "family_role": "GUARDIAN",
        "my_role": "GUARDIAN",
        "my_permissions": {
          "general": [
            "READ",
            "WRITE"
          ],
          "sensitive": [
            "READ"
          ],
          "private": [
            "READ"
          ]
        },
        "my_strict_permissions": {
          "general": [
            "READ",
            "WRITE"
          ],
          "sensitive": [
            "READ",
            "WRITE"
          ],
          "private": [
            "READ"
          ]
        },
        "rbac_migration_state": "shadow"
      }
    ],
    "rbac_migration_state": "shadow",
    "created_at": "2026-09-01T00:00:00Z",
    "updated_at": "2026-09-01T00:00:00Z"
  },
  "role_assignment": {
    "owner_id": "U9d8c7b6a5f4e3d2c1b0a9f8e7d6c5b4a",
    "is_complete": true,
    "unassigned_member_ids": [],
    "rbac_migration_state": "shadow"
  }
}
```

- 長輩那一列：`my_permissions.general` 有 `WRITE`（影子模式的 legacy 值），`my_strict_permissions.general` 只有 `READ` → 前端不顯示掛號的寫入按鈕，與後端的 403 一致。
- 爺爺那一列：委任解析成 GUARDIAN 的資料權限，`my_strict_permissions.general` 有 `WRITE`。

---

## 3. 新增欄位在各端點的呈現

| 欄位 | 型別 | nullable | 說明 |
|---|---|---|---|
| `cancelled_at` | string（ISO 8601，帶 `appointment_at` 的 offset，精確到秒） | 是 | `null`＝沒有取消 |
| `cancelled_by_user_id` | string | 是 | 與 `cancelled_at` 同時有值／同時為 `null`。取自取消者的 JWT，request 的任何參數都不影響它 |

| 端點 | 這兩個 key | 值 |
|---|---|---|
| `GET /reminders`（帶 scope：`items[]` 的每一筆；不帶：陣列的每一筆） | 永遠存在 | 取消的那筆有值，其餘 `null` |
| `POST /reminders` | 永遠存在 | `null`（剛建立不可能是取消的） |
| `PUT /reminders/{id}` | 永遠存在 | 取消的掛號仍可改備註等欄位，回應裡會保留原本的取消紀錄 |
| `POST .../depart`、`.../attend` | 永遠存在 | `null`（已取消的按不了，回 409） |
| `POST .../cancel` | 永遠存在 | 有值；重複按回第一次的值 |
| `DELETE`（單筆、全部） | 不回傳掛號 | — |

- 跨使用者讀取：兩個欄位在 `FIELD_CLASSIFICATION` 登記為 GENERAL，有 `GENERAL READ` 的家屬（含 MEMBER）看得到。
- 舊資料：上線前建立的文件沒有這兩個欄位，讀出來一律是 `null`，不需要遷移。
- LINE 推播與卡片都不顯示取消資訊（取消不發任何通知）。

---

## 4. 錯誤回應

body 格式與前一份報告相同：除了 422，一律是 `{"detail": "<一句完整的繁中句子>"}`，可直接顯示。

### 4.1 本次新增或改變的錯誤（完整清單）

| 端點 | 狀態 | 觸發 | detail 原文 |
|---|---|---|---|
| cancel | 403 | 不是本人，也沒有 `GENERAL WRITE`（含影子模式下的 MEMBER） | `您沒有權限替這位家人取消門診。` |
| cancel | 404 | id 不存在 | `找不到這筆掛號提醒，可能已經被刪除。` |
| cancel | 409 | 已到診 | `已經回報到診的門診不能取消。` |
| cancel | 409 | `missed`，或當日已結束（還沒被標記 missed 也一樣） | `這個門診的當天已經結束，不需要取消。` |
| PUT | 409 | 已取消的掛號改 `appointment_at`（**新**） | `已經取消的掛號不能改時間；如果要改期，請新增一筆掛號提醒。` |
| PUT | 409 | 已到診的掛號改 `appointment_at`（不變；讀取後、寫入前才到診也會回這個） | `已經回報到診的掛號不能改時間；如果是另一次門診，請新增一筆掛號提醒。` |
| POST、PUT（改了時間） | 409 | 同一位就診者、同一瞬間已有一筆沒取消的（**文案改了**，見 §7） | `這個時間已經有一筆掛號提醒，同一個時間只能有一筆。` |
| POST、PUT、DELETE 單筆 | 403 | 影子模式下的 MEMBER 現在也會拿到（**新**） | `您沒有權限替這位使用者設定掛號提醒。` |
| depart、attend | 403 | 同上（**新**，含 LINE 卡片按鈕） | `您沒有權限替這位家人回報出發或到診。` |
| depart、attend | 409 | 當日已結束、排程器還沒標記 missed（**新**；以前這段空窗會成功） | `這個門診的當天已經結束，無法再回報出發或到診。` |
| depart、attend | 409 | 已取消（**現在真的會發生**；門診前幾天按也回這個，不回「門診當天才能…」） | `這筆掛號提醒已經取消，無法回報出發或到診。` |
| GET（帶 scope=past） | 400 | `cursor` 被竄改或格式不對 | `分頁位置無效，請重新整理頁面後再試一次。` |
| GET | 422 | `limit` 不在 1–50、`scope` 不是 `upcoming`／`past` | FastAPI 預設格式（見下） |
| DELETE 全部 | 400 | 沒帶 `scope`，或 `scope` 不是 `past` | `一次刪除只適用於過去的掛號紀錄，請帶 scope=past。` |
| DELETE 全部 | 403 | 帶了別人的 `target_user_id`（包括空字串） | `只有本人可以刪除全部歷史紀錄。` |

- 順序：cancel 與其他單筆端點一樣，**先 404 後 403**。DELETE 全部先檢查 `scope`（400）再檢查是不是本人（403）。
- 401 與前一份報告相同（英文，沿用 `get_current_user`）。
- 沒有列在這裡的錯誤與前一份報告 §4 相同。

### 4.2 實際錄到的回應

| 情境 | 呼叫者 | 請求 | 狀態 | detail |
|---|---|---|---|---|
| POST 同一瞬間、不同醫院與科別 | 女兒（CAREGIVER） | `POST /api/appointments/reminders` | 409 | `這個時間已經有一筆掛號提醒，同一個時間只能有一筆。` |
| POST 兒子（MEMBER，影子模式）代建 | 兒子（MEMBER） | `POST /api/appointments/reminders` | 403 | `您沒有權限替這位使用者設定掛號提醒。` |
| cancel 兒子（MEMBER） | 兒子（MEMBER） | `POST /api/appointments/reminders/6aa4f0b0922e51d637c5b278/cancel` | 403 | `您沒有權限替這位家人取消門診。` |
| cancel 已到診 | 長輩本人 | `POST /api/appointments/reminders/6aa4f0af922e51d637c5b275/cancel` | 409 | `已經回報到診的門診不能取消。` |
| cancel 已錯過 | 長輩本人 | `POST /api/appointments/reminders/6aa4f0af922e51d637c5b276/cancel` | 409 | `這個門診的當天已經結束，不需要取消。` |
| cancel 不存在 | 長輩本人 | `POST /api/appointments/reminders/6aa27e77c964976ac9281fff/cancel` | 404 | `找不到這筆掛號提醒，可能已經被刪除。` |
| PUT 改已取消掛號的時間 | 長輩本人 | `PUT /api/appointments/reminders/6aa4f0b0922e51d637c5b279` | 409 | `已經取消的掛號不能改時間；如果要改期，請新增一筆掛號提醒。` |
| PUT 兒子（MEMBER） | 兒子（MEMBER） | `PUT /api/appointments/reminders/6aa4f0b0922e51d637c5b278` | 403 | `您沒有權限替這位使用者設定掛號提醒。` |
| DELETE 單筆 兒子（MEMBER） | 兒子（MEMBER） | `DELETE /api/appointments/reminders/6aa4f0b0922e51d637c5b278` | 403 | `您沒有權限替這位使用者設定掛號提醒。` |
| GET cursor 被竄改 | 長輩本人 | `GET /api/appointments/reminders?scope=past&cursor=e30` | 400 | `分頁位置無效，請重新整理頁面後再試一次。` |
| depart 兒子（MEMBER） | 兒子（MEMBER） | `POST /api/appointments/reminders/6aa4f0b0922e51d637c5b278/depart` | 403 | `您沒有權限替這位家人回報出發或到診。` |
| depart 已取消 | 長輩本人 | `POST /api/appointments/reminders/6aa4f0b0922e51d637c5b279/depart` | 409 | `這筆掛號提醒已經取消，無法回報出發或到診。` |
| attend 當日已結束、尚未標記 missed | 長輩本人 | `POST /api/appointments/reminders/6aa4f0b0922e51d637c5b27a/attend` | 409 | `這個門診的當天已經結束，無法再回報出發或到診。` |
| cancel 當日已結束、尚未標記 missed | 長輩本人 | `POST /api/appointments/reminders/6aa4f0b0922e51d637c5b27a/cancel` | 409 | `這個門診的當天已經結束，不需要取消。` |
| DELETE 全部 不帶 scope | 長輩本人 | `DELETE /api/appointments/reminders` | 400 | `一次刪除只適用於過去的掛號紀錄，請帶 scope=past。` |
| DELETE 全部 scope=upcoming | 長輩本人 | `DELETE /api/appointments/reminders?scope=upcoming` | 400 | `一次刪除只適用於過去的掛號紀錄，請帶 scope=past。` |
| DELETE 全部 女兒帶長輩 id | 女兒（CAREGIVER） | `DELETE /api/appointments/reminders?scope=past&target_user_id=U4af4980629d8f1f1e6bd8e0f7c4c5a1b` | 403 | `只有本人可以刪除全部歷史紀錄。` |

422 的實際 body：

Response `422`：

```json
{
  "detail": [
    {
      "type": "less_than_equal",
      "loc": [
        "query",
        "limit"
      ],
      "msg": "Input should be less than or equal to 50",
      "input": "51",
      "ctx": {
        "le": 50
      }
    }
  ]
}
```

Response `422`：

```json
{
  "detail": [
    {
      "type": "literal_error",
      "loc": [
        "query",
        "scope"
      ],
      "msg": "Input should be 'upcoming' or 'past'",
      "input": "all",
      "ctx": {
        "expected": "'upcoming' or 'past'"
      }
    }
  ]
}
```

---

## 5. 需要後端回覆的問題

（需求文件末尾列的是四個問題，逐一回覆。）

### Q1. `scope` 的參數名與 cursor 分頁方式是否符合後端既有慣例？

**參數名照提案，沒有改：`scope`、`limit`、`cursor`，回應 `items`、`next_cursor`、`total_count`。**

後端**沒有既有的 cursor 分頁**。唯一分頁過的端點是 admin 的知識回報佇列（`app/routers/admin/knowledge_reports.py`），用的是 `limit`／`offset`、回應 `{reports, total, limit, offset, status_counts}`。我沒有沿用 offset，理由是這個清單的成員會在翻頁期間移動：一筆門診跨過當日結束、被取消、或被刪除，offset 會讓下一頁重複或漏掉一筆。沿用的部分：`limit` 的範圍驗證照那支的寫法（`Query(ge=1, le=50)`，超出是 422）。

實作：
- cursor 是這一頁最後一筆的（門診時間, id），以 base64url 編成不透明字串。**請原樣帶回，不要解析**；格式日後可能改。
- 下一頁取「比它更舊」的：`appointment_at` 較早，或同一時間但 id 較小。同一時間多筆（例如取消後在原時間重掛）也不會跳過或重複，有測試釘住。
- 翻頁期間刪掉已顯示過的某一筆，下一頁不受影響（有測試）。翻頁期間才變成「過去」的門診（例如跨過午夜、或剛被取消）會排在已載入的那幾頁之前，不會出現在「載入更多」裡，重新整理後才看得到。
- 每一頁多取一筆來判斷有沒有下一頁，所以最後一頁的 `next_cursor` 直接是 `null`，不會多打一次空頁。
- 索引：沿用既有的 `(user_id, appointment_at)`。同一時間的 id 排序是在單一使用者的文件內做，以一個人一輩子的門診量不需要另建索引。

### Q2. 「刪除全部歷史紀錄」能否在單一操作內完成？失敗時的行為？

**是單一操作**：一個 `delete_many`，條件 `{user_id: 操作者本人, $or: [{day_end_at: {$lte: now}}, {status: "cancelled"}]}`，整個操作用同一個 `now`。這個條件與列表的 `scope=past` 是同一段程式碼產生的（`past_filter`）。

**但它不是全有全無。** MongoDB 的原子性以單一文件為單位，`delete_many` 不是交易：命令在伺服器端中途失敗（例如主節點切換）時，已經刪掉的不會還原，API 回 5xx。可以放心的是：
- 條件只挑得到過去的紀錄，**不論失敗在哪一刻，即將到來的門診一筆都不會被刪**。
- **重送同一個請求是安全的**：它把剩下的刪掉，回傳的 `deleted` 只算這一次刪掉的筆數。前端遇到 5xx 時可以直接重試，或重新 GET 讓畫面反映實際剩下多少。

若產品要求嚴格的全有全無，可以改成交易（Atlas 是 replica set，支援），但長輩的使用情境是「按了刪除，剩下的再按一次」，我判斷不值得多一層交易的複雜度，這次沒做。

另外，`deleted` 是**實際**刪除的筆數。畫面顯示 `total_count` 之後、按下確認之前若跨過某筆門診的當日結束，`deleted` 會比畫面上的數字多一筆。那不是兩份判定不一致，是時間往前走了。

### Q3. 需求五的洞 1、洞 2 是否同意照建議修正？

**同意，照建議修了。**

- **洞 1**：改時間的寫入改成 `find_one_and_update({_id, status ∈ {scheduled, departed, missed}})`。沒命中時重讀：文件不在 → 404；在 → 依狀態回「已到診不能改時間」或「已取消不能改時間」（到診與取消都是終局，重讀到的一定是讓條件落空的那個狀態）。讀取時就已經是到診／取消的，照舊在寫入前直接回 409。有兩條測試模擬「讀完之後、寫入之前有人按了到診／取消」，確認紀錄沒被洗掉、`notify_at` 仍是空的。
- **洞 2**：`mark_departed`、`mark_attended` 與新的 `mark_cancelled` 的條件都加上 `day_end_at > now`。沒命中且當日已結束時回 `missed` 的 409。

一個細節與建議的字面不同：**「重複按同一顆」在當日結束後仍是 200 冪等**。例如已到診的門診，隔天凌晨家屬又按一次「我已到診」，回 200、內容不變——這一下沒有寫入任何東西，擋的是「轉移」，不是「查看現況」。只有真的要改變狀態的按法會回 409。

### Q4. 嚴格判定的權限欄位最後的名稱與形狀

**名稱 `my_strict_permissions`（照提案），形狀與 `my_permissions` 完全相同。** 位置：`GET /api/family/me` → `family_tree.family_members[]` 的每一位，與 `my_permissions` 並列。實際輸出見 §2.12。

```json
"my_strict_permissions": {
  "general": ["READ", "WRITE"],
  "sensitive": ["READ"],
  "private": []
}
```

- 值是純 RBAC：`is_allowed(角色, 分類, 動作)`，角色含委任解析（有效委任 → GUARDIAN 的資料權限），**不受遷移狀態影響**。與 `can()` 是同一個判定；沒有逐位呼叫 `can()`，是因為那會變成每位成員各查一次族譜的 N+1，`describe_members` 原本就為了避免這個而批次查詢。
- 前端用法：**掛號的新增、編輯、取消、單筆刪除、出發／到診按鈕，只看 `my_strict_permissions.general` 有沒有 `"WRITE"`**。「刪除全部歷史紀錄」不看權限，只在本人自己的頁面顯示。
- **注意它不是「更嚴的 my_permissions」**：兩份的判定依據不同，某些格子嚴格那份反而比較寬。例如受委任者在影子模式下，`my_permissions.sensitive` 是 `["READ"]`（legacy），`my_strict_permissions.sensitive` 是 `["READ", "WRITE"]`（§2.12 爺爺那一列）。它回答的是「以嚴格判定的路徑，後端會不會放行」，只該用在那些路徑上（目前就是掛號的寫入）。其他功能的按鈕請維持看 `my_permissions`。
- 同樣不構成授權；後端每支端點仍各自判定。

---

## 6. 與需求文件的差異清單

| # | 需求文件 | 實際做法 | 為什麼 |
|---|---|---|---|
| 1 | 嚴格權限「由後端的 `can()` 算出來」 | 同一個判定（`is_allowed` + 含委任的角色解析），但在 `describe_members` 裡批次算，不逐位呼叫 `can()` | 逐位呼叫是 N+1；語意相同 |
| 2 | 「當日結束後沒命中回 missed 409」（洞 2） | 已經是目標狀態的重複按仍回 200 冪等 | 那一下不寫入任何東西；見 Q3 |
| 3 | `limit` 上限 50 | 超出範圍回 FastAPI 的 422，不是自動截成 50 | 沿用既有分頁端點的驗證方式；前端照規格送就不會遇到 |
| 4 | （未規定）`cursor` 格式錯誤 | 400 `分頁位置無效，請重新整理頁面後再試一次。` | 竄改或過期的 cursor 不能靜默回第一頁，否則「載入更多」會重複顯示 |
| 5 | （未規定）GET 的 `scope` 不合法 | 422（FastAPI 預設） | DELETE 的 scope 照規格回 400；GET 的 scope 是列舉參數，交給框架驗證 |
| 6 | 刪除全部：「省略 `target_user_id`＝本人」 | 省略或等於本人才放行；**空字串視為別人 → 403** | 空字串多半是前端沒取到長輩的 id，同樣不能落到操作者自己的紀錄上 |
| 7 | 刪除全部「盡量在同一個操作裡完成」 | 單一 `delete_many`，但不是交易 | 見 Q2 |
| 8 | （未規定）重複的 409 文案 | 由「同醫院、同科別」改成 `這個時間已經有一筆掛號提醒，同一個時間只能有一筆。` | 規則改了，舊文案會說錯 |
| 9 | 需求四「改了 appointment_at 的 PUT 撞到時回 409」 | 只在瞬間真的改變時檢查；整份回送原本的時間不檢查 | 舊規則下可能已經存在同一瞬間的兩筆（不同科）。編輯表單會整份回送 `appointment_at`，若照樣檢查，那些舊資料連備註都改不了。新規則不溯及既往 |
| 10 | （未規定）已取消的未來門診按出發／到診 | 回「已取消」，不回「門診當天才能回報」 | 錄製 JSON 時發現原本的判定順序會回後者，讓人以為當天還按得下去 |
| 11 | 推播收件人「兩種模式下都是 GUARDIAN 與 CAREGIVER」 | 照做，做法是在授權模型新增 `STRICT_NOTIFICATION_KINDS`（目前只有 `appointment_reminder`），而不是在排程器裡特判 | 與 `authorize` 的 `has_legacy_equivalent=False` 同一個概念；日後其他導入後才有的推播可以加入同一張表 |
| 12 | （未規定）排程器已搶下、正在送的推播 | 取消後仍會送達；卡片上的按鈕回「已取消」的純文字 | 需求五第 5 點已說可以接受，這裡註明 |

「已拍板」清單沒有任何一條被更動。

---

## 7. 需求四是本次做的，還是之前就有？

**本次做的。** 之前只擋「同一時間、同醫院、同科別」：前一份報告 §2.2 寫明「同一時間但不同醫院或不同科別**不擋**」，程式碼是 `AppointmentService._is_same_appointment`（有 `facility_id` 比 id，沒有才比院名，並忽略空白）。2026-09-10 的追加並沒有實作。

現在的規則：同一位就診者、同一個 `appointment_at` 瞬間，只能有一筆**沒有取消**的掛號，不論醫院或科別。POST 撞到回 409；PUT 只在門診時間真的改到一個已被佔用的瞬間時回 409（見 §6 第 9 點）。已取消的不算，取消後可以在原時間重掛（§2.4）。

仍是應用層的檢查，不是唯一索引：規則收斂成「同一瞬間」之後，部分唯一索引其實表達得了，但舊規則下可能已經存在同一瞬間的兩筆，建索引會失敗。兩個請求在同一瞬間送出時仍可能都通過；前端已先擋，這裡是清單過期時的最後一道防線。

---

## 8. 變更到既有行為的地方，與既有測試怎麼改

| # | 既有行為 | 現在 | 既有測試的處理 |
|---|---|---|---|
| 1 | PUT 改已取消掛號的時間 → 狀態回 `scheduled`（前一份報告 §2.7／§3.2） | 409 | 確認過：**沒有任何既有測試釘住「已取消可以改時間」**（當時沒有路徑能寫入 cancelled，測試裡也沒有這個情境）。新增 `test_a_cancelled_appointment_cannot_be_moved`（服務層，另驗證取消後仍可改備註、整份回送原時間不算改時間）與 `test_put_new_time_on_a_cancelled_reminder_is_a_409`（router）。router 的 PUT 說明文字與 openspec spec 一併更新 |
| 2 | 同一時間不同醫院或不同科別可以並存 | 409 | `test_same_time_different_department_is_allowed`、`test_same_name_but_different_facility_is_allowed`（原本斷言可以建）→ 改成 `test_same_instant_is_a_duplicate_whatever_the_hospital_or_department`，三種情形都斷言 409。`test_duplicate_check_ignores_spacing_in_names`、`test_same_facility_id_is_a_duplicate_even_if_the_name_was_edited` 刪除（它們測的院名／id 比對邏輯已不存在）。`test_cancelled_reminder_does_not_count`（原本直接改資料庫假裝取消）→ `test_a_cancelled_reminder_frees_its_time_slot`（走真的 cancel）。router 的 `test_creating_the_same_appointment_twice_is_a_409` → `test_a_second_appointment_at_the_same_instant_is_a_409`（第二筆換醫院與科別，斷言新文案）。新增 `test_rows_that_predate_the_rule_stay_editable` |
| 3 | 影子模式下 MEMBER 可以新增、修改、刪除、代按出發／到診 | 403 | router 的 `test_member_in_shadow_mode_keeps_the_legacy_write`（原本斷言 200）→ 改成 `test_every_write_path_is_strict_in_shadow_mode`：6 條寫入路徑 × MEMBER／CAREGIVER／GUARDIAN。新增 LINE 卡片按鈕的 `test_card_buttons_use_the_strict_check_in_shadow_mode`、影子模式 MEMBER 仍可讀的 `test_member_in_shadow_mode_can_still_read` |
| 4 | 影子模式下掛號推播送族譜全員 | 只送 GUARDIAN、CAREGIVER | 排程器的 `test_shadow_mode_keeps_the_legacy_whole_family`（原本斷言含 MEMBER）與強制模式那條合併成 `test_family_recipients_are_the_general_writers_in_both_modes[enforced/shadow]`。授權服務新增 `test_appointment_recipients_are_filtered_even_in_shadow`，同時斷言高風險藥物通報在影子模式下仍送全員；既有的 `test_notification_recipients_keep_whole_family_in_shadow` 沒改、仍然通過 |
| 5 | 當日結束後、標記 missed 前，出發／到診會成功 | 409 | 沒有既有測試涵蓋這段空窗。新增服務層與 repository 的邊界測試 |
| 6 | 改時間是無條件寫入 | 條件式寫入 | 新增兩條競態測試；既有改時間的測試不變、仍通過 |
| 7 | 出發／到診衝突時，未列舉的狀態一律回「已取消」 | 依狀態回；scheduled／departed 只可能出現在當日已結束時，回「當天已經結束」 | 原本的 fallback 沒有測試。`test_reporting_on_a_cancelled_appointment_is_a_conflict`（直接改資料庫假裝取消）→ `test_a_cancelled_appointment_accepts_neither_button`（走真的 cancel，並驗證門診前一天、當天、當日結束後三個時點都回「已取消」） |
| 8 | 回應 21 個 key | 23 個 | router 測試的 `RESPONSE_KEYS` 加入兩個 key。欄位分類的守門測試（`tests/unit/models/test_family_authorization.py`）自動涵蓋，未改 |
| 9 | `/api/family/me` 的成員沒有嚴格權限 | 多 `my_strict_permissions` | 既有兩條端點測試各加一行斷言（強制模式下與 `my_permissions` 相同；族譜外的人為空）。服務層新增四條 |
| 10 | 不帶 scope 的 `include_past=false` 不會出現 cancelled | 會出現取消了的未來門診 | 判定本身沒改。新增 `test_the_legacy_list_is_unchanged_by_cancelling` 釘住「舊路徑照舊只看當日結束」 |

---

## 9. 其他注意事項

- **已經送出的 LINE 卡片**：前一份報告 §9 的處理在 `cancelled` 真的會被寫入之後依然正確——按「我已出發／我已到診」回純文字「這筆掛號提醒已經取消，無法回報出發或到診。」（按的人自己的語言，六種），不寫入。測試走真的 cancel：`test_a_card_left_on_the_phone_after_cancelling`。
- **取消與其他動作同時發生**：cancel、depart、attend 都是對同一組來源狀態的條件式原子更新，只會有一邊寫入，另一邊重讀後回冪等或 409。cancel 與改時間同時：改時間的條件排除 `cancelled`，所以取消後的瞬間不會被 PUT 復活（§5 Q3）。
- **「即將到來」與「過去」互斥且涵蓋全部**：repository 測試 `test_upcoming_and_past_are_disjoint_and_cover_everything` 用 5 種狀態 × 3 個門診時間，在 4 個時點（含當日結束的那一刻）驗證；`test_delete_past_uses_the_same_definition_as_the_list` 驗證刪除全部刪的就是列表的 past。「即將到來」在程式裡是「過去」的 `$nor` 補集，不是另一組條件，所以兩者在結構上就不會不一致。
- **尚未做**：部署到 care-dev、以真實 LINE 帳號走一次取消流程（openspec tasks 7.7 與既有的 6.2）。
