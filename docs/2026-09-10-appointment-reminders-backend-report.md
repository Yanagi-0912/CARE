# 掛號提醒後端實作報告（回覆前端 2026-09-09 需求文件）

日期：2026-09-10
狀態：已實作、單元測試全綠，**尚未 commit、尚未部署**
對應 openspec：`openspec/changes/appointment-reminders/`

---

## 0. 先看這裡

### 需要前端／產品確認的事項

以下幾點都**沒有**動到「已拍板」清單，但它們是文件沒寫到、而我做了判斷的地方。前端寫 UI 前請先看過：

1. **家屬收到的卡片會寫出就診者的名字**（例如「王媽媽 的門診」）。已拍板清單寫的是「推播文案不含科別、醫師、看診號」，這三樣確實都沒有。但建議定案那一段寫「只含日期時間與醫院名稱」，比拍板內容更嚴。一個家屬可能同時照顧兩位長輩，不寫名字就不知道是誰的門診；用藥的家屬警報也有寫名字。若產品要照字面拿掉，請告訴我。
2. **「當日結束」在深夜門診上有一個邊界解讀。** 23:45 的門診若照字面在 00:00 標記 missed，T+30 的家屬警報（00:15）永遠發不出去，兩條規則互相矛盾。我的做法是：當日結束＝當地午夜，但不早於門診後 60 分鐘。23:00 以前的門診完全不受影響。
3. **`cancelled` 狀態目前沒有任何路徑會寫入。** 文件的狀態機列了它，但 API 清單沒有「取消」這個動作。見 §8。
4. **出發／到診要在門診當天才能按**（門診當地日 00:00 與 T-1h 取較早者）。這是我加的防呆：一週前手滑按了「我已到診」，會把後續推播全部取消，正是這個功能要防止的結果。
5. **家屬收件人沒辦法「沿用用藥提醒的 resolver」，因為用藥根本沒有 resolver。** 用藥的 T+30 家屬警報只送給提醒的建立者（`creator_user_id`）一個人。見 §5 第 3 題。

### 改了哪些檔案

新增：
- `app/models/appointment.py`
- `app/repositories/appointment_repository.py`
- `app/repositories/push_claim.py`（推播重試上限，從用藥抽出來共用）
- `app/services/appointment/appointment_service.py`
- `app/services/appointment/appointment_scheduler.py`
- `app/services/scheduling/push_tick_scheduler.py`（排程骨架，從用藥抽出來共用）
- `app/services/line_messaging/flex/appointment_flex.py`
- `app/routers/users/appointments.py`
- `tests/unit/appointment/`（8 個檔案）
- `openspec/changes/appointment-reminders/`

修改：
- `app/services/medication/medication_scheduler.py`、`app/repositories/medication_repository.py`：改為沿用共用實作，行為不變，既有測試一行沒改仍全綠。
- `app/models/family_authorization.py`：新增資源 `appointment_reminder` 與推播種類 `appointment_reminder`。
- `app/routers/users/medical.py`：新增 `GET /facilities/{facility_id}`。
- `app/i18n/messages.py`：新增掛號卡片文案，六種語言。
- `app/services/line_messaging/dispatcher/dispatcher.py`：新增 postback。
- `app/main.py`、`app/dependencies.py`、`app/db/mongodb.py`：接線。
- `tests/unit/models/test_family_authorization.py`：守門測試納入新模型。

### 測試

- `pytest tests/`：**4001 passed、1 failed**。
- 唯一的失敗是 `test_require_magick_passes_when_binary_is_present`，要本機裝 ImageMagick 才會過。在沒有任何改動的 HEAD 上同樣失敗（3590 passed、1 failed），與本次無關。
- 新增 411 條測試。repository 與排程器用記憶體 collection，對每一個時間窗邊界做真實查詢。這個 collection 會模擬 Motor 的 naive UTC 讀回。
- 注意：專案裡的 `.venv` 缺 `python-multipart` 與 `Pillow`，router 測試會全部 collection error。要用 `venv/Scripts/python.exe -m pytest tests/`。
- 本報告 §2 與 §4 的 JSON 全部是測試用 client 打**真實 app** 錄下來的輸出：真的 router、真的服務層、真的 `FamilyAuthorizationService`，只把資料庫換成記憶體。沒有手寫的範例。

---

## 1. 最終 API 清單

**認證（全部 7 支都一樣）**：`Authorization: Bearer <JWT>`。JWT 由既有的 `POST /api/auth/liff/login` 簽發，與用藥 API 相同。

| # | 方法 | 完整路徑 | Query 參數 | Body | 成功回應 | 授權規則 |
|---|---|---|---|---|---|---|
| 1 | GET | `/api/appointments/reminders` | `target_user_id`（選填，省略＝本人）<br>`include_past`（選填，`true`／`false`，預設 `false`） | — | `200`，陣列 | 查本人：無條件。查別人：對他有 `GENERAL READ` |
| 2 | POST | `/api/appointments/reminders` | — | 見 §2 | `200`，單筆 | 對 body 的 `user_id` 有 `GENERAL WRITE`（本人一定有） |
| 3 | PUT | `/api/appointments/reminders/{reminder_id}` | — | 部分更新，見 §2 | `200`，單筆 | 對**就診者**（不是建立者）有 `GENERAL WRITE` |
| 4 | DELETE | `/api/appointments/reminders/{reminder_id}` | — | — | `200`，`{"ok": true}` | 同 PUT |
| 5 | POST | `/api/appointments/reminders/{reminder_id}/depart` | — | 不需要（帶了也忽略） | `200`，單筆 | 本人，或對就診者有 `GENERAL WRITE` 的家屬 |
| 6 | POST | `/api/appointments/reminders/{reminder_id}/attend` | — | 不需要（帶了也忽略） | `200`，單筆 | 同 depart |
| 7 | GET | `/api/medical/facilities/{facility_id}` | — | — | `200`，單筆院所 | 已登入即可（與 `/facilities?keyword=` 相同） |

補充：
- `GET` 依 `appointment_at` 由早到晚排序。
- `include_past=false` 回傳「當日結束還沒到」的門診，也就是**今天**（包含今天稍早已看完的）與未來。這條界線與排程器標記 `missed` 用的是同一條（§7）。`include_past=true` 回傳全部。
- `POST` 成功回 `200`，不是 `201`，與用藥的 `POST /api/medications/reminders` 一致。
- 各角色的 `GENERAL WRITE`：強制模式下 OWNER（本人）、GUARDIAN、CAREGIVER 有，MEMBER 沒有；影子模式下族譜成員都有。前端用既有的 `canWriteGeneral` 判斷即可。

---

## 2. 請求／回應實際 JSON

以下 id 是實際產生的 ObjectId 字串。`U4af49…`＝長輩本人，`Ub2c7e…`＝女兒（CAREGIVER）。

### 2.1 `POST /api/appointments/reminders`（本人建立）

伺服器時間 `2026-09-14T10:00:00+08:00`，呼叫者＝長輩本人。

Request：
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
  "id": "6aa27e77c964976ac9281c0f",
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

### 2.2 `POST`（家屬代建；`facility_id` 與所有選填欄位皆為 null）

呼叫者＝女兒。

Request：
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
  "id": "6aa27e77c964976ac9281c10",
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
  "status": "scheduled",
  "departed_at": null,
  "departed_by_user_id": null,
  "attended_at": null,
  "attended_by_user_id": null,
  "enabled": true,
  "notify_at": [
    "2026-09-18T13:10:00+08:00",
    "2026-09-18T14:10:00+08:00",
    "2026-09-18T14:40:00+08:00"
  ],
  "created_at": "2026-09-14T10:00:00+08:00",
  "updated_at": "2026-09-14T10:00:00+08:00"
}
```

POST body 的規則：
- 必填：`user_id`、`appointment_at`、`hospital_name`、`department`。缺 key → 422（§4）。
- 選填的六個欄位可以不帶，也可以帶 `null`，兩者效果相同。
- 字串一律去頭尾空白。選填欄位送 `""` 或全空白，會存成 `null`。

### 2.3 `GET /api/appointments/reminders`（預設，`include_past=false`）

伺服器時間 `2026-09-14T10:00:00+08:00`。資料庫裡另有一筆 9/1 已到診的舊門診，因為已經過去，所以不在清單內。

Response `200`：
```json
[
  {
    "id": "6aa27e77c964976ac9281c0f",
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
    "id": "6aa27e77c964976ac9281c10",
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
    "status": "scheduled",
    "departed_at": null,
    "departed_by_user_id": null,
    "attended_at": null,
    "attended_by_user_id": null,
    "enabled": true,
    "notify_at": [
      "2026-09-18T13:10:00+08:00",
      "2026-09-18T14:10:00+08:00",
      "2026-09-18T14:40:00+08:00"
    ],
    "created_at": "2026-09-14T10:00:00+08:00",
    "updated_at": "2026-09-14T10:00:00+08:00"
  }
]
```

### 2.4 `GET /api/appointments/reminders?include_past=true`

與 2.3 相同，前面多出那筆 9/1 的舊門診。舊門診是已到診狀態，因此 `notify_at` 是空陣列。只列出多出來的那一筆：

```json
{
  "id": "6aa27e77c964976ac9281c11",
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
  "enabled": true,
  "notify_at": [],
  "created_at": "2026-08-29T10:00:00+08:00",
  "updated_at": "2026-08-29T10:00:00+08:00"
}
```

這一筆 `departed_at` 是 `null`，`attended_at` 有值，代表沒按出發就直接回報到診，這是合法的。

### 2.5 `GET /api/appointments/reminders?target_user_id=U4af49…`（家屬查長輩）

形狀與 2.3 **完全相同**，包含科別、醫師、看診號。掛號提醒的每個欄位都是 GENERAL，所以有 `GENERAL READ` 的人（含 MEMBER）看到的就是完整資料。

### 2.6 `PUT /api/appointments/reminders/{id}`：清空醫師、改備註

Request：
```json
{
  "doctor_name": null,
  "note": "記得帶健保卡"
}
```

Response `200`：`doctor_name` 被清空，`note` 被改掉，**沒帶的 `serial_number` 等欄位不動**。
```json
{
  "id": "6aa27e77c964976ac9281c0f",
  "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "creator_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "appointment_at": "2026-09-15T09:30:00+08:00",
  "facility_id": "abc123",
  "hospital_name": "台大醫院",
  "hospital_address": "臺北市中正區中山南路7號",
  "hospital_phone": "0223123456",
  "department": "心臟內科",
  "doctor_name": null,
  "serial_number": "23",
  "note": "記得帶健保卡",
  "status": "scheduled",
  "departed_at": null,
  "departed_by_user_id": null,
  "attended_at": null,
  "attended_by_user_id": null,
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

`PUT {}`（空 body）不做任何事，原樣回傳，`updated_at` 也不變。

### 2.7 `PUT`：改門診時間（已出發 → 狀態重置）

情境：伺服器時間 `2026-09-18T13:20:00+08:00`，這筆已經按過出發（`status: "departed"`、`departed_at: "2026-09-18T13:20:00+08:00"`）。

Request：
```json
{
  "appointment_at": "2026-09-25T14:10:00+08:00"
}
```

Response `200`：狀態回到 `scheduled`，出發紀錄清空，`notify_at` 依新時間重算。
```json
{
  "id": "6aa27e77c964976ac9281c10",
  "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "creator_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
  "appointment_at": "2026-09-25T14:10:00+08:00",
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
  "enabled": true,
  "notify_at": [
    "2026-09-25T13:10:00+08:00",
    "2026-09-25T14:10:00+08:00",
    "2026-09-25T14:40:00+08:00"
  ],
  "created_at": "2026-09-14T10:00:00+08:00",
  "updated_at": "2026-09-18T13:20:00+08:00"
}
```

PUT 可帶的 key：`appointment_at`、`facility_id`、`hospital_name`、`hospital_address`、`hospital_phone`、`department`、`doctor_name`、`serial_number`、`note`、`enabled`。
- **不能**改：`user_id`、`creator_user_id`、`status` 與出發／到診欄位。狀態只能透過 depart／attend 改。
- 未知的 key 會被**靜默忽略**，不回 422，與用藥 PUT 相同。

### 2.8 `DELETE /api/appointments/reminders/{id}`

Response `200`：
```json
{
  "ok": true
}
```

同一個 id 再刪一次 → `404`（§4）。

### 2.9 `POST /api/appointments/reminders/{id}/depart`

伺服器時間 `2026-09-15T08:40:00+08:00`，呼叫者＝女兒（代按）。不需要 body。

Response `200`：
```json
{
  "id": "6aa27e77c964976ac9281c0f",
  "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "creator_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "appointment_at": "2026-09-15T09:30:00+08:00",
  "facility_id": "abc123",
  "hospital_name": "台大醫院",
  "hospital_address": "臺北市中正區中山南路7號",
  "hospital_phone": "0223123456",
  "department": "心臟內科",
  "doctor_name": null,
  "serial_number": "23",
  "note": "記得帶健保卡",
  "status": "departed",
  "departed_at": "2026-09-15T08:40:00+08:00",
  "departed_by_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
  "attended_at": null,
  "attended_by_user_id": null,
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

**同一顆按鈕被第二個人按**（本人隨後也按了 depart）：回 `200`，內容與上面**逐字相同**。`departed_by_user_id` 仍然是女兒，第二下不會覆寫第一位回報者，也不會回錯誤。

### 2.10 `POST /api/appointments/reminders/{id}/attend`

伺服器時間 `2026-09-15T09:35:00+08:00`，呼叫者＝本人。

Response `200`：`notify_at` 變成空陣列，代表後續推播全部取消。
```json
{
  "id": "6aa27e77c964976ac9281c0f",
  "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "creator_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "appointment_at": "2026-09-15T09:30:00+08:00",
  "facility_id": "abc123",
  "hospital_name": "台大醫院",
  "hospital_address": "臺北市中正區中山南路7號",
  "hospital_phone": "0223123456",
  "department": "心臟內科",
  "doctor_name": null,
  "serial_number": "23",
  "note": "記得帶健保卡",
  "status": "attended",
  "departed_at": "2026-09-15T08:40:00+08:00",
  "departed_by_user_id": "Ub2c7e1d0a9f8e7d6c5b4a3928170f6e5",
  "attended_at": "2026-09-15T09:35:00+08:00",
  "attended_by_user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b",
  "enabled": true,
  "notify_at": [],
  "created_at": "2026-09-14T10:00:00+08:00",
  "updated_at": "2026-09-15T09:35:00+08:00"
}
```

attend 第二次（女兒再按）→ `200`，內容與上面逐字相同，`attended_by_user_id` 仍是本人。

### 2.11 `GET /api/medical/facilities/{facility_id}`

形狀與 `/facilities?keyword=`、`/nearby` 回傳陣列中的**每一筆完全相同**，含 `business_status`。`distance_meters` 一律 `null`，因為沒有座標。

Response `200`：
```json
{
  "id": "665f1c2e8b3e4a0012345678",
  "name": "國立臺灣大學醫學院附設醫院",
  "latitude": 25.0408,
  "longitude": 121.5188,
  "address": "臺北市中正區中山南路7號",
  "phone": "02-23123456",
  "type": "醫院",
  "clinic_time": {
    "monday":    { "isClosed": false, "slots": [ { "open": "08:30", "close": "12:00" }, { "open": "13:30", "close": "17:30" } ] },
    "tuesday":   { "isClosed": false, "slots": [ { "open": "08:30", "close": "12:00" }, { "open": "13:30", "close": "17:30" } ] },
    "wednesday": { "isClosed": false, "slots": [ { "open": "08:30", "close": "12:00" }, { "open": "13:30", "close": "17:30" } ] },
    "thursday":  { "isClosed": false, "slots": [ { "open": "08:30", "close": "12:00" }, { "open": "13:30", "close": "17:30" } ] },
    "friday":    { "isClosed": false, "slots": [ { "open": "08:30", "close": "12:00" }, { "open": "13:30", "close": "17:30" } ] },
    "saturday":  { "isClosed": false, "slots": [ { "open": "08:30", "close": "12:00" } ] },
    "sunday":    { "isClosed": true,  "slots": [] }
  },
  "departments": ["心臟內科", "家庭醫學科", "急診醫學科"],
  "notes": null,
  "distance_meters": null,
  "business_status": {
    "status": "closed_today",
    "next_open": { "weekday_key": "friday", "time_text": "08:30", "is_today": false },
    "note": null,
    "has_emergency": true
  }
}
```

（`clinic_time` 為了排版壓成單行，實際輸出是一般縮排的 JSON，內容相同。）

---

## 3. 回應欄位表

### 3.1 掛號提醒（2.1–2.10 的每一筆）

**缺 key 與 null 的語意：在目前的實作裡，回應中的每一個 key 永遠存在，沒有值就是 `null`。前端不會遇到「沒有這個 key」的情況，兩者不需要區分。**

唯一的例外是**未來**：跨使用者讀取（`target_user_id` 是別人）在強制模式下會經過欄位遮蔽，而遮蔽的做法是**拿掉 key**，不是設成 null。目前所有欄位都登記為 GENERAL，有讀取權就一律看得到，所以今天不會發生。但如果日後新增一個分類更高的欄位，權限不足的人會**缺這個 key**。那時「缺 key」的意思是「你沒有權限看」，「null」的意思是「沒有填」。建議前端型別現在就把跨使用者讀取的欄位視為 optional，或至少不要在缺 key 時崩潰。

| 欄位 | 型別 | nullable | 說明／null 的語意 |
|---|---|---|---|
| `id` | string | 否 | 24 碼 hex（ObjectId 字串） |
| `user_id` | string | 否 | 就診者 LINE userId |
| `creator_user_id` | string | 否 | 建立者（可能是家屬）。只是來源紀錄，**不是**授權依據 |
| `appointment_at` | string（ISO 8601，帶 offset） | 否 | 精確到分鐘，秒數一律 `:00`。offset 就是建立（或最後一次改時間）時送來的那個 |
| `facility_id` | string | 是 | null＝建立時沒有院所 id（查詢結果沒帶，或使用者後來清掉） |
| `hospital_name` | string | 否 | 權威顯示值 |
| `hospital_address` | string | 是 | null＝沒填 |
| `hospital_phone` | string | 是 | null＝沒填 |
| `department` | string | 否 | 科別 |
| `doctor_name` | string | 是 | null＝沒填 |
| `serial_number` | string | 是 | null＝沒填 |
| `note` | string | 是 | null＝沒填 |
| `status` | `"scheduled"`／`"departed"`／`"attended"`／`"missed"`／`"cancelled"` | 否 | 見 3.2 |
| `departed_at` | string（ISO 8601，帶 offset） | 是 | null＝還沒回報出發；或直接回報到診沒按出發；或改了門診時間被重置 |
| `departed_by_user_id` | string | 是 | 與 `departed_at` 同時有值／同時為 null |
| `attended_at` | string（ISO 8601，帶 offset） | 是 | null＝還沒回報到診 |
| `attended_by_user_id` | string | 是 | 與 `attended_at` 同時有值／同時為 null |
| `enabled` | boolean | 否 | `false`＝推播全部停止（狀態機照走，見 §6） |
| `notify_at` | string[]（ISO 8601，帶 offset） | 否（可能是空陣列） | **固定 3 筆或 0 筆**，見下方 |
| `created_at` | string（ISO 8601，帶 offset） | 否 | 精確到秒 |
| `updated_at` | string（ISO 8601，帶 offset） | 否 | 精確到秒 |

`notify_at` 的語意：
- 3 筆：依序是 `[T-1h, T+0, T+30]`，包含已經送過的時間點。收件人：
  - `[0]` T-1h：本人＋家屬
  - `[1]` T+0：本人＋家屬
  - `[2]` T+30：**只有家屬**，而且只在到時仍未到診才會發
- 0 筆：不會再有任何推播。可能是 `enabled=false`，或 `status` 是 `attended`、`missed`、`cancelled` 之一。
- 前端的「我們會在這些時間提醒你」可以直接「有就顯示、空的就不顯示」。但第三筆不是給本人的，文案請寫成像「家人會在 10:00 收到未到診通知」這類。

### 3.2 `status` 狀態機

```
scheduled ──depart──▶ departed ──attend──▶ attended
    │                     │
    └───────attend────────┘
scheduled／departed ──（當日結束）──▶ missed
cancelled：本版沒有任何路徑會寫入（§8）
改門診時間：scheduled／departed／missed／cancelled ──▶ scheduled（attended 不能改時間）
```

### 3.3 院所（2.11）

與既有 `FacilityPayload` 相同，本次沒有改動任何欄位：
- `clinic_time` 可為 null。
- 每一天的 key 是 `monday`–`sunday`，`slots[].open`／`close` 是沒有 offset 的 `"HH:MM"`，意思是院所當地（台灣）時間。
- `business_status.status` 的列舉值不變（`app/services/medical/business_hours.py` 的 `BusinessStatus`）：`open`、`before_open`、`break`、`closed_today`、`closed_day`、`emergency`、`call_ahead`、`unknown`。

---

## 4. 錯誤回應

**body 格式**：除了 422，一律是：
```json
{ "detail": "<一句完整的繁中句子>" }
```

`detail` 只有繁中版，API 路由沒有語言中介層，這點與用藥 API 相同。LINE 那一側的同一組錯誤有六種語言（§9）。

下表每一個 detail 都是**實際回應的原文**，可以直接顯示給使用者。

### 400

| 端點 | 觸發 | detail 原文 |
|---|---|---|
| POST、PUT | `appointment_at` 沒有 offset | `門診時間必須帶時區，例如 2026-09-15T09:30:00+08:00。` |
| POST、PUT（改時間時） | `appointment_at` 不晚於現在 | `門診時間已經過了，請確認日期與時間。` |
| POST、PUT | `hospital_name` 是空字串或全空白 | `醫院名稱不能是空的。` |
| POST、PUT | `department` 是空字串或全空白 | `科別不能是空的。` |
| POST、PUT | 超過長度 | `{欄位名}最多 {N} 個字。`，例如 `備註最多 500 個字。` |
| PUT | 對不可為 null 的欄位送 `null` | `以下欄位不接受空值：{欄位名（key）、…}`，例如：`以下欄位不接受空值：門診時間（appointment_at）、科別（department）、提醒開關（enabled）、醫院名稱（hospital_name）` |

長度上限與錯誤訊息裡用的欄位名：

| key | 欄位名 | 上限 |
|---|---|---|
| `facility_id` | 院所代碼 | 64 |
| `hospital_name` | 醫院名稱 | 100 |
| `hospital_address` | 醫院地址 | 200 |
| `hospital_phone` | 醫院電話 | 30 |
| `department` | 科別 | 50 |
| `doctor_name` | 醫師 | 50 |
| `serial_number` | 看診號 | 20 |
| `note` | 備註 | 500 |

「不接受空值」清單依 key 的字母順序排列。可能出現的 key 有 `appointment_at`（門診時間）、`department`（科別）、`enabled`（提醒開關）、`hospital_name`（醫院名稱）。

### 401（沿用既有 `get_current_user`，英文）

| 觸發 | detail 原文 |
|---|---|
| 沒帶 Authorization | `Missing Authorization header` |
| 不是 `Bearer xxx` 格式 | `Invalid Authorization header format` |
| JWT 過期 | `Token expired` |
| JWT 無效 | `Invalid token` |
| JWT 內容缺使用者 | `Invalid token payload` |

### 403

| 端點 | detail 原文 |
|---|---|
| GET（查別人） | `您沒有權限查看這位使用者的掛號提醒。` |
| POST、PUT、DELETE | `您沒有權限替這位使用者設定掛號提醒。` |
| depart、attend | `您沒有權限替這位家人回報出發或到診。` |

### 404

| 端點 | detail 原文 |
|---|---|
| PUT、DELETE、depart、attend | `找不到這筆掛號提醒，可能已經被刪除。` |
| `GET /api/medical/facilities/{id}` | `查無此院所資料，可能已被更新或移除。` |

注意順序：先查存在與否（404），再查權限（403），與用藥 API 相同。所以一個無權的人拿不存在的 id 會拿到 404，拿存在的 id 會拿到 403。

### 409

| 端點 | 觸發 | detail 原文 |
|---|---|---|
| PUT | 已到診（`attended`）的掛號改 `appointment_at` | `已經回報到診的掛號不能改時間；如果是另一次門診，請新增一筆掛號提醒。` |
| depart、attend | 門診當地日 00:00 與 T-1h 兩者較早者之前 | `門診當天才能回報出發或到診。` |
| depart | 狀態已是 `attended` | `已經回報到診了，不需要再回報出發。` |
| depart、attend | 狀態是 `missed` | `這個門診的當天已經結束，無法再回報出發或到診。` |
| depart、attend | 狀態是 `cancelled` | `這筆掛號提醒已經取消，無法回報出發或到診。` |

「重複按同一顆」**不是** 409：depart 遇到 `departed`、attend 遇到 `attended`，一律 200 冪等回傳。

### 422（FastAPI 預設格式，**不是**給使用者看的）

只在請求形狀本身錯誤時出現，前端型別正確就不會遇到。`detail` 是陣列，`msg` 是英文：

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "hospital_name"],
      "msg": "Field required",
      "input": { "user_id": "U4af4980629d8f1f1e6bd8e0f7c4c5a1b", "appointment_at": "2026-09-15T09:30:00+08:00", "...": "..." }
    }
  ]
}
```

```json
{
  "detail": [
    {
      "type": "datetime_from_date_parsing",
      "loc": ["body", "appointment_at"],
      "msg": "Input should be a valid datetime or date, invalid character in year",
      "input": "明天早上九點",
      "ctx": { "error": "invalid character in year" }
    }
  ]
}
```

### 503（只有院所端點）

`GET /api/medical/facilities/{id}` 的服務層拋出例外時：`醫療院所查詢暫時不可用，請稍後再試`

但 repository 的 `find_by_id` 本身會把資料庫錯誤吞掉並回 None。這是 LINE「查看院所詳情」既有的行為，我沒有動。所以實務上**資料庫故障會表現成 404**，不是 503（見 §8）。

---

## 5. 六個問題的答覆

### Q1. 欄位命名與路徑是否可行？有沒有既有慣例要沿用？

可行，路徑與欄位**全部照提案**，沒有改任何一個名字。沿用的既有慣例：

- `id` 是字串 `id`，不是 `_id`。用藥 API 曾經因為 alias 讓前端讀到 `_id`、打出 `/reminders/undefined`，這次的回應模型沒有 alias，不會重演。
- 查別人用 query 參數 `target_user_id`，與 `GET /api/medications/reminders` 相同。
- `DELETE` 回 `{"ok": true}`，`POST` 回 `200`，都與用藥相同。
- 403 走同一個 `FamilyAuthorizationService`，但 detail 換成掛號專用文案（§4）。原本的訊息會寫出 `GENERAL`、`WRITE` 這種代號。

唯一新增、提案裡沒有的回應欄位：無。`notify_at` 本來就在提案裡。

### Q2. `notify_at` 由後端算好隨 `GET` 回傳，可行嗎？

可行，已實作。而且**不只 GET**：POST、PUT、depart、attend 的回應都有，因為所有端點共用同一個回應模型，前端拿 POST 的回應直接更新列表即可。

語意見 §3.1：固定 3 筆（T-1h、T+0、T+30）或 0 筆（不會再推）。時間字串的 offset 與 `appointment_at` 相同。前端不需要任何常數：T-1h、T+30 這兩個長度只存在後端 `app/models/appointment.py` 一處。

### Q3. 家屬的收件人 resolver 能否與用藥提醒共用同一份？

**不能照字面共用，因為用藥提醒沒有「家屬 resolver」。** 用藥的 T+30 家屬警報與停機彙整通知，收件人是 `MedicationLog.alert_notify_user_id`，而它就是 `reminder.creator_user_id`：只送給**建立那筆提醒的人一個人**。

- 長輩自己設的用藥提醒，逾時警報會送回長輩本人。
- 女兒幫忙設的，就只有女兒收得到，兒子收不到。

我改用專案裡**真正共用的那一個** resolver：`FamilyAuthorizationService.notification_recipients`，高風險藥物、非處方藥、緊急事件三種家屬通報都走它。我在 `NOTIFICATION_POLICY` 新增了種類 `appointment_reminder`：
- **強制模式**：收件人是 GUARDIAN 與 CAREGIVER，也就是對就診者有 `GENERAL WRITE`、按得下卡片按鈕的人。MEMBER 只有讀取權，收到的會是一張按下去必定 403 的卡片，所以不納入。
- **影子模式**：族譜全員收。與 `authorize` 的 legacy 判定一致，影子模式下全員也都按得下按鈕。
- 本人一定不在家屬名單中，本人收的是本人版卡片。
- T-1h、T+0、T+30 三個階段**呼叫同一個函式、同一個種類**，不會有兩套名單。有測試釘住。

**文件擔心的那個不一致確實存在，但它是用藥那一側原本就有的**：同一個家庭裡，掛號沒去會通知 GUARDIAN／CAREGIVER（影子模式下是全家），用藥沒吃只通知當初設定的那個人。要讓兩邊一致，應該把用藥改走同一個 resolver。但那會改變用藥的既有行為：誰會突然開始收到、誰會突然收不到。這不是這次該默默做的事，建議另開一個 change。

### Q4. 能否補一支 `GET /api/medical/facilities/{id}`？

已補，形狀見 2.11，與列表中的每一筆完全相同。後端本來就有 `MedicalService.get_facility_by_id`，LINE 的「查看院所詳情」postback 在用，只是沒有 HTTP 端點。

前端走這條，不需要 keyword 搜尋的退路。唯一的注意事項：資料庫故障時這支會回 404 而不是 503（§4、§8）。

### Q5. 推播量：現有的推播配額或節流機制吃得下嗎？

**後端沒有任何推播配額或節流機制**，只有「單一推播階段最多重試 5 次」的上限。這是用藥提醒在 2026-08 打爆 LINE 月額度之後加的，這次抽出來與掛號共用。LINE 方案的月額度是多少、還剩多少，是維運面的數字，程式碼裡看不到，我無法替你們判斷「吃不吃得下」。能說的是：

- **每次門診的上限**：本人 2 則（T-1h、T+0）＋ 每位家屬 3 則（T-1h、T+0、T+30）＝ `2 + 3N` 則。
  - 例：兩位 CAREGIVER → 最多 8 則。
  - 按下「我已到診」之後的推播都不會發，所以正常情況下 T+30 不會發，實際約 `2 + 2N`。
- **影子模式會放大這個數字。** 目前預設是影子模式，全域開關 `FAMILY_RBAC_ENFORCED` 關閉時，所有家庭都當作影子模式。這時收件人是**族譜全員**，包含 MEMBER。在大部分家庭切到強制模式之前，實際推播量會比「只有 GUARDIAN／CAREGIVER」多。
- **不會重複推**：
  - 推播權是原子搶佔，多實例並存也只推一次。
  - 部分收件人失敗（例如某位家屬封鎖了官方帳號）**不重試**，避免其他人被同一則連環轟炸。
  - 只有全部失敗才重試，最多 5 次後放棄。
- **按鈕的回覆走 reply，不是 push**：「已記錄出發／到診」卡片是用 reply token 回覆的。依 LINE 官方文件，reply 不計入月推播額度。這一點是平台的說法，本 repo 沒有實測驗證。
- 可以壓量的現成開關：本人的 `notify_reminder`、家屬的 `notify_family`，後端都有讀，關了就不送。

建議：上線第一週觀察 `appointment_reminders` 的推播筆數，與既有的用藥推播量合併看月額度。openspec tasks 6.3 已列為待辦。

### Q6. 時區能一併處理嗎？

**掛號提醒：完整處理**。細節見 §7：
- 必須帶 offset。
- 排程用瞬間判定。
- 「當日結束」與推播顯示用使用者送來的 offset。
- API 原樣回傳 offset。

**用藥提醒：文件的前提已經過時。** 「用藥提醒全程以 UTC 判定、08:00 在台灣 16:00 推播」這個問題，已在 `1f3cbc87`、`b2887477` 兩個 commit 修掉：
- 排程器以 `Asia/Taipei` 判定觸發時間。
- `openspec/specs/medication-reminders` 有「推播的時區與顯示設定」一條明文要求。
- 單元測試 `test_reminder_flex_shows_taipei_time_not_utc` 釘住這件事。
- 前端「刻意不做補償」是對的，不需要做。

**但兩支 API 的時間語意確實不同**，前端需要知道。這次沒有動用藥 API：

| | 掛號提醒 | 用藥提醒 |
|---|---|---|
| 排定時間的表示 | `appointment_at`：完整 ISO 8601，帶 offset | `scheduled_time`：`"HH:MM"`，沒有日期、沒有 offset，意思是**台北時間**的時鐘 |
| 其他 datetime 欄位（`created_at`、`taken_at`、`scheduled_at`…） | 帶 offset，與 `appointment_at` 相同 | **UTC，但不一定帶 offset**：剛建立的物件輸出 `2026-09-10T01:23:45.678000Z`，從資料庫讀回的輸出 `2026-09-10T01:23:45.678000`（**沒有 offset，但它是 UTC**） |
| 秒以下 | 無（精確到秒） | 有（微秒） |

前端解析用藥 API 的 datetime 時，沒有 offset 的一律要當 UTC，不能當本地時間。這是用藥那一側的既有行為，修正它會改動用藥 API 的輸出，建議與 Q3 的 resolver 一起另開 change。

---

## 6. 與需求文件的差異清單

| # | 需求文件的提案 | 實際做法 | 為什麼 |
|---|---|---|---|
| 1 | 家屬 resolver 沿用用藥提醒現有的那份 | 改用 `FamilyAuthorizationService.notification_recipients`，新種類 `appointment_reminder` | 用藥沒有家屬 resolver，只送 creator。見 Q3 |
| 2 | 「用藥全程 UTC，這次只能處理掛號也請明說」 | 用藥早已改用台北時間；兩支 API 的差異在輸出格式 | 見 Q6 |
| 3 | 推播文案「只含日期時間與醫院名稱」 | 家屬版多一行就診者名稱；T+30 警報在已出發時寫出出發時間（例如 08:40）；科別、醫師、看診號、備註一律沒有 | 不寫名字，家屬不知道是誰的門診。已拍板清單只排除科別、醫師、看診號。**請確認**（§0 第 1 點） |
| 4 | 當日結束 → missed | 當日結束＝當地午夜，但不早於門診後 60 分鐘 | 23:00 以後的門診，照字面的話 T+30 警報發不出去。**請確認**（§0 第 2 點） |
| 5 | 狀態 `cancelled` | 列舉值保留，但沒有任何路徑會寫入 | API 清單沒有取消的動作；我不擅自加端點。見 §8 |
| 6 | PUT nullable 欄位：五個 | 六個：多 `facility_id` | 使用者改選一家查不到 id 的院所時，必須能把舊的 `facility_id` 清掉，否則會指向錯的院所 |
| 7 | （未規定）門診時間的驗證 | 必須帶 offset（400）、必須晚於現在（400）、秒數捨去 | 沒有 offset 無法知道是哪裡的 09:30；過去的時間建了也不會有任何推播 |
| 8 | （未規定）字串欄位 | 去頭尾空白；選填欄位的空字串存成 null；必填欄位不能是空的；有長度上限 | 讓「沒有值」只有 null 一種表示法 |
| 9 | （未規定）出發／到診的時間限制 | 門診當天（或 T-1h）以前回 409 | 防手滑提早按「到診」而取消所有提醒（§0 第 4 點） |
| 10 | （未規定）重複按 | 同一個目標狀態 → 200 冪等，保留第一位回報者 | 本人與家屬按的是同一組按鈕，兩人各按一次是常態 |
| 11 | （未規定）改門診時間 | 狀態重置為 `scheduled`、清除出發紀錄、三個推播階段重新排定；`attended` 不能改時間（409）；同一瞬間換一個 offset 不算改時間 | 舊時間的「已出發」帶到新時間，會讓新時間的 T-1h 不發 |
| 12 | （未規定）`enabled` | 只控制推播：關閉後三個階段都不送，但仍可按出發／到診，當日結束仍會標記 missed | 狀態記的是「當天有沒有人回報到診」這件事實，與要不要推播無關 |
| 13 | T+0 未出發：「推播催促」 | 催促卡片附兩顆按鈕：「我已到診」（主）＋「我已出發」（次） | 人可能已經到了只是沒按出發；文件也允許 attend 從 scheduled 直接轉 |
| 14 | T+30：「家屬警報」 | 警報卡片附「我已到診」 | 家屬可能就在長輩旁邊，只是兩人都忘了按 |
| 15 | （未規定）晚建立與停機 | 每個階段有自己的時間窗（T-1h～T+0、T+0～T+30、T+30～當日結束），過了就跳過不補推 | 門診前 20 分鐘才建立 → 下一分鐘送出 T-1h 卡片，文案不寫「一小時後」；停機跨過 T+0 → 重啟時不會連發三則 |
| 16 | （未規定）推播部分失敗 | 至少一人送達就算完成，不重試；全部失敗才重試（最多 5 次） | 某位家屬封鎖官方帳號時，重試會讓其他人重複收到 |
| 17 | `include_past` | 已實作；「過去」＝當日結束已過（今天稍早看完的診仍在預設清單裡，直到當天結束） | 與 missed 的界線一致 |
| 18 | 403 沿用用藥的檢查 | 同一個授權服務，detail 換成掛號文案 | 用藥的 403 訊息寫的是 `GENERAL`、`WRITE` 代號 |
| 19 | 沿用雙階遞進排程引擎 | 把用藥排程器的迴圈、心跳、推播權搶佔、收件人偏好抽成 `PushTickScheduler`，重試上限抽成 `push_claim`；兩個排程器共用同一份實作，但各自一個 task、各自一個心跳 | 「沿用」若是複製貼上，日後改一邊必漏改另一邊。心跳分開，是讓一邊卡住時看得出是哪一邊 |

---

## 7. 時區語意

**API 收（request）**
- `appointment_at` **必須**是帶 offset 的 ISO 8601：`2026-09-15T09:30:00+08:00`。`Z` 也接受，等於 `+00:00`。
- 不帶 offset → 400。
- 秒與秒以下一律捨去：送 `09:30:45` 會存成 `09:30:00`。
- 請不要送數字（unix timestamp）：pydantic 會當成 UTC 接受，但那不是本契約的一部分。

**API 發（response）**
- 掛號提醒回應裡的**每一個** datetime 都帶 offset，而且都用 `appointment_at` 的 offset 表示，包括：`appointment_at`、`notify_at[]`、`departed_at`、`attended_at`、`created_at`、`updated_at`。
- 前端送 `+08:00`，就全部拿回 `+08:00`，原樣顯示即可，不需要換算。
- 精確度：`appointment_at` 與 `notify_at` 到分鐘，其餘到秒，都沒有小數。
- 用 PUT 送「同一瞬間、不同 offset」的 `appointment_at`，只會換掉輸出用的 offset，狀態不動。
- 院所端點的 `clinic_time.*.slots[].open/close` 是沒有 offset 的 `"HH:MM"`，意思是院所當地的時鐘時間（台灣）。

**排程實際怎麼判定**
- 資料庫存的是 UTC 瞬間（MongoDB 的 Date），另外存一個 `appointment_utc_offset_minutes`（台灣＝480）。
- T-1h、T+0、T+30 是「門診瞬間 ± 固定長度」和「現在的瞬間」比大小，**與伺服器時區、UTC、台北都無關**。所以不會有「差 8 小時」這類錯誤。有測試：UTC 00:30 的 tick 會觸發台北 09:30 門診的 T-1h，卡片寫 09:30 而不是 01:30。
- 真正需要「當地」的只有兩處，**都用使用者送來的那個 offset**：
  1. 「當日結束」的午夜落在哪一刻，決定何時標記 missed，以及 `include_past` 的界線；
  2. 推播卡片上顯示的日期、星期、時間。
- 使用者 profile 裡**沒有**時區設定，整個系統也沒有「每位使用者的時區」這個概念；用藥是寫死 `Asia/Taipei`。掛號採「送來的 offset」而不是寫死台北，是為了讓 API 原樣回傳，前端不需要換算。前端照需求文件「原樣送」即可。

---

## 8. 這次沒做的部分，與前端的降級建議

| 沒做的事 | 影響 | 前端建議 |
|---|---|---|
| `cancelled` 沒有寫入路徑（沒有取消端點） | 目前資料裡不會出現 `cancelled` | 型別保留這個值，UI 當成終局狀態顯示「已取消」即可。要取消一次門診請用 DELETE；只想停止推播用 `PUT {"enabled": false}`。若產品要保留「取消過」的紀錄，告訴我再加 `POST .../cancel` |
| API 的錯誤 `detail` 只有繁中 | LIFF 選日文等語言時，直接顯示 detail 會是中文 | 非繁中語系請依「端點 + 狀態碼」顯示前端自己的譯文，對不上時才退回 detail。§4 的表已列出每個 409 只對應一種情境：depart 的 409 看 detail 區分三種，attend 的 409 區分兩種 |
| 沒有 `GET /reminders/{id}` 單筆查詢 | 編輯頁不能單獨重抓一筆 | 從列表拿，需要時帶 `include_past=true`。PUT、depart、attend 的回應就是最新的單筆 |
| 用藥的家屬警報仍只送 creator（Q3） | 同一家庭，掛號與用藥的通知對象不同 | 設定頁若要說明「誰會收到通知」，兩個功能請分開寫 |
| 用藥 API 的 datetime 輸出沒修（Q6） | 用藥的 datetime 有時沒有 offset | 沒有 offset 的用藥 datetime 一律當 UTC 解析 |
| 院所端點在資料庫故障時回 404 | 前端會以為院所不存在 | 404 時退回提案裡的方案 B（keyword 搜尋），文案寫「查不到這家院所的最新資料」，不要斷定「院所已不存在」 |
| 卡片上沒有「在 LIFF 查看詳情」連結按鈕 | 家屬要自己打開 LIFF 才看得到科別等資訊 | 無。若要加，請給 LIFF 深連結路徑（例如 `/reminders/appointments`），我在卡片加一顆 URI 按鈕 |
| 有人按了出發／到診，不會推播通知另一方 | 女兒代按到診，長輩手機不會收到「已到診」 | LIFF 列表顯示 `departed_by_user_id`／`attended_by_user_id` 對應的名字；頁面回到前景時重新 GET |
| 沒有推播節流、沒有配額監控（Q5） | 人多的家庭、影子模式下推播量較大 | 無 |
| PUT 的未知 key 靜默忽略，不回 422 | 拼錯 key 不會報錯 | 靠 TypeScript 型別擋 |
| 422 是 FastAPI 預設的英文陣列 | 不能直接顯示 | 前端做輸入驗證（必填、長度，見 §4）；真的遇到 422 就顯示通用錯誤 |
| 尚未 commit、尚未部署到 care-dev、尚未用真實 LINE 帳號走過一次完整流程 | — | openspec tasks 6.2 列為待辦；前端可以先照本報告寫型別 |

---

## 9. Flex 卡片的 postback 行為

### 卡片與按鈕

| 卡片 | 送給誰 | 按鈕 | postback `data` |
|---|---|---|---|
| T-1h「門診提醒」 | 本人＋家屬 | 我已出發 | `action=appointment_depart&appointment_id=<id>` |
| T+0「門診時間到了」（已出發） | 本人＋家屬 | 我已到診 | `action=appointment_attend&appointment_id=<id>` |
| T+0「還沒出發嗎？」（未出發） | 本人＋家屬 | 我已到診（主）、我已出發（次） | 同上兩種 |
| T+30「尚未確認到診」 | 家屬 | 我已到診 | `action=appointment_attend&appointment_id=<id>` |
| 按完之後的「已記錄出發／到診」 | 按的那個人 | **沒有按鈕** | — |

實際的按鈕節點（T-1h 家屬版的 footer，錄自實際輸出）：
```json
{
  "type": "postback",
  "label": "我已出發",
  "data": "action=appointment_depart&appointment_id=6aa27e77c964976ac9281c0f",
  "displayText": "我已出發"
}
```

- 本人與家屬收到的是**同一組按鈕、同一個 `data`**。
- 卡片內容只有：日期時間（例如「9/15（週二）09:30」）、醫院名稱。家屬版多一行「王媽媽 的門診」。
- altText 例：`【王媽媽】門診提醒：9/15（週二）09:30 台大醫院`。
- 科別、醫師、看診號、備註不在任何一則推播裡，altText 也沒有。這由兩層守住：
  - builder 的參數根本沒有這些欄位；
  - 一條測試讓一筆「精神科」門診走完三個階段，檢查每一則推播的全文。

### 按下去之後發生什麼

1. LINE 把 postback 事件送到既有的 LINE webhook，**不會打 HTTP API**，不需要 JWT。`displayText`（「我已出發」「我已到診」）會以使用者的名義出現在聊天室裡。
2. dispatcher 依 `action` 呼叫 `AppointmentService.depart` 或 `attend`。這**與 `POST .../depart`、`POST .../attend` 是同一個方法**，授權判定、狀態檢查、門診當天的限制完全相同。按下的人就是回報者，寫入 `departed_by_user_id`／`attended_by_user_id`。
3. 成功，或「已經是這個狀態」的冪等情形：bot 用 **reply**（不是 push，見 Q5）回一張新的「已記錄」卡片：
   - 灰色 header「已記錄出發」或「已記錄到診」。
   - 時間、醫院的區塊；按的人不是本人時，多一行「王媽媽 的門診」。
   - 提示：出發後是「到了之後記得按「我已到診」」；到診後是「這次門診後續的提醒已全部停止」。
   - 底部一行「08:40　由 您 回報」，或別人先按過時的「08:40　由 王小明 回報」。
4. 失敗：bot 回一則**純文字**，用按的人自己的語言（六種）。繁中原文與 §4 的 detail 相同：
   - 無權限：`您沒有權限替這位家人回報出發或到診。`
   - 已到診後按出發：`已經回報到診了，不需要再回報出發。`
   - 已錯過：`這個門診的當天已經結束，無法再回報出發或到診。`
   - 已取消：`這筆掛號提醒已經取消，無法回報出發或到診。`
   - 太早：`門診當天才能回報出發或到診。`
   - 已刪除：`找不到這筆掛號提醒，可能已經被刪除。`

### 按完卡片會不會變

**原本那張卡片不會變。** LINE 不支援修改已經送出的訊息，卡片上的按鈕之後仍然按得下去。

- 「變」的方式是多一則回覆，也就是那張沒有按鈕的「已記錄」卡片，只有按的那個人看得到。
- 其他收件人手機上的卡片也不會變。他們之後再按，會拿到：
  - 同樣的「已記錄」卡片（冪等，上面寫著原本是誰在幾點回報的）；或
  - 一則說明文字（例如已經到診了還按出發）。
- 狀態一旦變成 `attended`，**所有尚未發出的推播都不會再發**，本人與家屬都一樣：T+0、T+30 的查詢與搶佔都只挑 `scheduled`／`departed`。按了「我已出發」則只取消 T-1h（如果還沒發），T+0 會改發「我已到診」卡片，T+30 仍會在未到診時發給家屬。
- LIFF 頁面不會被即時通知狀態改變，頁面回到前景時請重新 GET。
