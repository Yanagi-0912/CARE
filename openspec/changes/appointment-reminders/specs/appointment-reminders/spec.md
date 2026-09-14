## ADDED Requirements

### Requirement: 掛號提醒是單次事件

每筆掛號提醒 SHALL 對應一次門診，含就診者（`user_id`）與建立者（`creator_user_id`）兩個獨立欄位。`creator_user_id` 只是來源紀錄，SHALL NOT 構成授權依據。

`appointment_at` SHALL 以帶 offset 的 ISO 8601 收發，SHALL 截到分鐘。不帶 offset 的請求 SHALL 以 400 拒絕；早於現在的門診時間 SHALL 以 400 拒絕。`facility_id` SHALL 可為 null，SHALL NOT 作為外鍵。門診時間 SHALL NOT 以院所的 `clinic_time` 驗證。

系統 SHALL NOT 儲存病名、主訴或診斷。

同一位就診者、同一個門診瞬間 SHALL 只能有一筆狀態不是 `cancelled` 的提醒，不論醫院或科別是否相同；建立、或把門診時間改到已被佔用的瞬間時 SHALL 回 409。只改其他欄位、或整份回送原本的門診時間，SHALL NOT 觸發這項檢查。

#### Scenario: 家屬與本人各建了一次同一張掛號單

- **WHEN** 已有一筆 9/15 09:30 台大醫院心臟內科的提醒，家屬又建立同一筆
- **THEN** 第二筆 SHALL 回 409，SHALL NOT 建立

#### Scenario: 同一時間、另一家醫院

- **WHEN** 同一時間已有一筆「台大醫院」的提醒，又建立一筆「馬偕醫院」
- **THEN** 系統 SHALL 回 409

#### Scenario: 取消後在原時間重掛

- **WHEN** 9/15 09:30 那一筆已取消，再建立一筆 9/15 09:30
- **THEN** 系統 SHALL 照常建立

#### Scenario: 帶 offset 原樣回傳

- **WHEN** 建立時送出 `2026-09-15T09:30:00+08:00`
- **THEN** 之後每一次讀取的 `appointment_at` SHALL 為 `2026-09-15T09:30:00+08:00`

#### Scenario: 不在院所門診時段內的時間

- **WHEN** 使用者填了 09:47，且該院所當天的 `clinic_time` 不含這個時段
- **THEN** 系統 SHALL 照常建立

### Requirement: 部分更新區分「沒帶」與「null」

PUT SHALL 以 `exclude_unset` 取值：沒帶的欄位 SHALL NOT 變動，帶了 null 的欄位 SHALL 清空。可為 null 的欄位只有 `facility_id`、`hospital_address`、`hospital_phone`、`doctor_name`、`serial_number`、`note`；其餘欄位送 null SHALL 回 400。

改動 `appointment_at`（瞬間不同）SHALL 讓狀態回到 `scheduled`、清除出發與到診紀錄、重新排定三個推播階段。狀態為 `attended` 或 `cancelled` 時 SHALL 拒絕改動門診時間（409）。改門診時間的寫入 SHALL 以「狀態仍為 `scheduled`／`departed`／`missed`」為條件；讀取之後、寫入之前狀態變成 `attended` 或 `cancelled` 時 SHALL 回 409，SHALL NOT 覆寫。

#### Scenario: 清空醫師

- **WHEN** PUT `{"doctor_name": null}`
- **THEN** `doctor_name` SHALL 為 null，其他欄位 SHALL NOT 變動

#### Scenario: 改時間與回報到診同時發生

- **WHEN** 家屬送出改時間的 PUT，服務讀到現況之後、寫入之前，就診者按了「我已到診」
- **THEN** PUT SHALL 回 409，到診紀錄 SHALL 保持不變

### Requirement: 提醒節奏

排程 SHALL 以門診瞬間加減固定長度判定，與伺服器時區無關：

- T-1h：狀態為 `scheduled` 時，推給本人與家屬，附「我已出發」
- T+0：狀態為 `departed` 時附「我已到診」；為 `scheduled` 時為催促，附「我已到診」與「我已出發」；皆推給本人與家屬
- T+30：狀態不是 `attended` 時推給家屬
- 當日結束（門診所在當地日的午夜，但不早於門診後 60 分鐘）：狀態仍為 `scheduled` 或 `departed` 者 SHALL 標記為 `missed`，不推播

每個階段 SHALL 只在自己的時間窗內推播（T-1h 至 T+0、T+0 至 T+30、T+30 至當日結束）；時間窗已過的階段 SHALL 跳過，不補推。

`enabled=false` SHALL 停止所有推播，但狀態機照常運作。

#### Scenario: 出發了卻沒到

- **WHEN** 就診者 08:40 回報出發，10:00（T+30）時仍未回報到診
- **THEN** 家屬 SHALL 收到警報，文案 SHALL 寫出 08:40 已出發

#### Scenario: 到診後停止一切推播

- **WHEN** 09:10 回報到診
- **THEN** SHALL NOT 再有 T+0 推播與 T+30 家屬警報

#### Scenario: 停機後重啟

- **WHEN** 服務於 08:00 至 10:05 停機，10:05 執行第一個 tick
- **THEN** 只有 T+30 家屬警報 SHALL 送出，T-1h 與 T+0 SHALL NOT 補推

### Requirement: 家屬收件人

家屬 SHALL 由 `FamilyAuthorizationService.notification_recipients(就診者, "appointment_reminder")` 決定；T-1h、T+0、T+30 SHALL 使用同一份名單。合格角色 SHALL 為 GUARDIAN 與 CAREGIVER（含受委任者），強制模式與影子模式皆然——影子模式 SHALL NOT 擴及族譜全員。本人 SHALL NOT 出現在家屬名單中。

每位收件人 SHALL 依自己的設定：本人看 `notify_reminder`，家屬看 `notify_family`；語言與字級取收件人自己的設定。

### Requirement: 寫入授權一律嚴格

掛號提醒的每一條寫入路徑——建立（為他人）、修改、單筆刪除、出發、到診、取消，含 LINE 卡片按鈕——SHALL 以 `has_legacy_equivalent=False` 判定：只有對就診者有 GENERAL 寫入權者（本人、GUARDIAN、CAREGIVER、受委任者）SHALL 可執行，影子模式下亦同。讀取 SHALL 維持預設判定。

#### Scenario: 影子模式下的 MEMBER

- **WHEN** 族譜處於影子模式，只有讀取權的 MEMBER 替長輩按「我已到診」
- **THEN** 系統 SHALL 回 403，SHALL NOT 寫入

族譜成員的回應 SHALL 另附 `my_strict_permissions`：形狀與 `my_permissions` 相同，為不受遷移狀態影響、含委任解析的純 RBAC 權限。它 SHALL NOT 構成授權。

### Requirement: 出發與到診

本人 SHALL 可回報；對就診者有 GENERAL 寫入權者 SHALL 可代為回報。實際按下的人 SHALL 寫入 `departed_by_user_id`／`attended_by_user_id`。

`depart` SHALL 只接受 `scheduled`；`attend` SHALL 接受 `scheduled` 或 `departed`；兩者 SHALL 另以「當日結束尚未到」為條件。已是目標狀態時 SHALL 冪等回傳（200），SHALL NOT 覆寫原本的回報者；`attended`／`missed`／`cancelled` 不是合法來源、或當日已結束時 SHALL 回 409。門診當地日 00:00 與 T-1h 兩者較早者之前 SHALL 回 409。

LIFF 的 `POST .../depart`、`POST .../attend` 與 LINE 卡片的 postback SHALL 呼叫同一個服務方法。

#### Scenario: 隔天凌晨補按到診

- **WHEN** 門診當日已結束、排程器尚未標記 missed，有人按下「我已到診」
- **THEN** 系統 SHALL 回 409，狀態 SHALL NOT 變成 `attended`

### Requirement: 取消這次門診

`POST .../cancel` SHALL 把 `scheduled`／`departed` 且當日尚未結束的提醒轉為 `cancelled`，寫入 `cancelled_at` 與 `cancelled_by_user_id`（取自 token），並停止所有尚未發出的推播。系統 SHALL NOT 因取消發出任何推播。取消 SHALL NOT 限於門診當天。

已是 `cancelled` 時 SHALL 冪等回傳，SHALL NOT 覆寫第一位取消者；`attended`、`missed` 或當日已結束 SHALL 回 409。

#### Scenario: 取消後按下舊卡片

- **WHEN** 取消之後，有人按下先前收到的 T-1h 卡片上的「我已出發」
- **THEN** 系統 SHALL 回覆已取消的說明，SHALL NOT 寫入

### Requirement: 列表分為即將到來與過去

「過去」SHALL 只有一份定義：當日已結束，或狀態為 `cancelled`。「即將到來」SHALL 為其補集。列表與「刪除全部歷史紀錄」SHALL 使用同一份定義。

`GET /reminders` 帶 `scope=upcoming` 時 SHALL 由早到晚回傳全部；帶 `scope=past` 時 SHALL 由新到舊、以不透明的 cursor 分頁，`limit` 預設 20、上限 50。帶 `scope` 時回應 SHALL 為 `{items, next_cursor, total_count}`，`total_count` 為整個 scope 的筆數。不帶 `scope` 時 SHALL 維持舊格式（陣列、`include_past` 語意不變）。系統 SHALL NOT 對過去的紀錄設時間上限。

#### Scenario: 取消了下個月的門診

- **WHEN** 一筆下個月的門診被取消
- **THEN** 它 SHALL 出現在 `scope=past`，SHALL NOT 出現在 `scope=upcoming`

### Requirement: 刪除全部歷史紀錄

`DELETE /reminders?scope=past` SHALL 以單一操作刪除操作者本人「過去」的全部提醒，回傳 `{"deleted": 實際筆數}`；沒有可刪的時 SHALL 回 `{"deleted": 0}`。即將到來的提醒 SHALL NOT 被刪除。`scope` 缺漏或不是 `past` 時 SHALL 回 400。

只有本人 SHALL 可執行：`target_user_id` 省略或等於操作者才放行，帶了他人的 id SHALL 回 403，SHALL NOT 改刪操作者自己的紀錄。

### Requirement: 推播的隱私邊界

任何推播（含 altText）SHALL NOT 含科別、醫師、看診號或備註。推播 SHALL 只含日期時間、醫院名稱，家屬版另含就診者的顯示名稱。

#### Scenario: 精神科門診

- **WHEN** 一筆科別為「精神科」的提醒走完三個推播階段
- **THEN** 本人與家屬收到的每一則訊息 SHALL NOT 出現「精神科」

### Requirement: 推播時間點由後端提供

每筆回應 SHALL 附 `notify_at`：還會推播時為 `[T-1h, T+0, T+30]`（以 `appointment_at` 的 offset 表示），不會再推播（`enabled=false` 或狀態為 `attended`／`missed`／`cancelled`）時為空陣列。
