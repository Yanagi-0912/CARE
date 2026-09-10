## ADDED Requirements

### Requirement: 掛號提醒是單次事件

每筆掛號提醒 SHALL 對應一次門診，含就診者（`user_id`）與建立者（`creator_user_id`）兩個獨立欄位。`creator_user_id` 只是來源紀錄，SHALL NOT 構成授權依據。

`appointment_at` SHALL 以帶 offset 的 ISO 8601 收發，SHALL 截到分鐘。不帶 offset 的請求 SHALL 以 400 拒絕；早於現在的門診時間 SHALL 以 400 拒絕。`facility_id` SHALL 可為 null，SHALL NOT 作為外鍵。門診時間 SHALL NOT 以院所的 `clinic_time` 驗證。

系統 SHALL NOT 儲存病名、主訴或診斷。

同一位就診者、同一個門診瞬間、同醫院、同科別 SHALL 只能有一筆狀態不是 `cancelled` 的提醒，建立或修改成重複時 SHALL 回 409。醫院在兩邊都有 `facility_id` 時 SHALL 以 id 比對，否則以院名比對；院名與科別比對時 SHALL 忽略空白。同一瞬間但醫院或科別不同 SHALL NOT 視為重複。

#### Scenario: 家屬與本人各建了一次同一張掛號單

- **WHEN** 已有一筆 9/15 09:30 台大醫院心臟內科的提醒，家屬又建立同一筆
- **THEN** 第二筆 SHALL 回 409，SHALL NOT 建立

#### Scenario: 同名的連鎖分院

- **WHEN** 同一時間已有一筆「仁愛診所」（facility_id A），又建立「仁愛診所」（facility_id B）同一科
- **THEN** 系統 SHALL 照常建立

#### Scenario: 帶 offset 原樣回傳

- **WHEN** 建立時送出 `2026-09-15T09:30:00+08:00`
- **THEN** 之後每一次讀取的 `appointment_at` SHALL 為 `2026-09-15T09:30:00+08:00`

#### Scenario: 不在院所門診時段內的時間

- **WHEN** 使用者填了 09:47，且該院所當天的 `clinic_time` 不含這個時段
- **THEN** 系統 SHALL 照常建立

### Requirement: 部分更新區分「沒帶」與「null」

PUT SHALL 以 `exclude_unset` 取值：沒帶的欄位 SHALL NOT 變動，帶了 null 的欄位 SHALL 清空。可為 null 的欄位只有 `facility_id`、`hospital_address`、`hospital_phone`、`doctor_name`、`serial_number`、`note`；其餘欄位送 null SHALL 回 400。

改動 `appointment_at`（瞬間不同）SHALL 讓狀態回到 `scheduled`、清除出發與到診紀錄、重新排定三個推播階段。狀態為 `attended` 時 SHALL 拒絕改動門診時間（409）。

#### Scenario: 清空醫師

- **WHEN** PUT `{"doctor_name": null}`
- **THEN** `doctor_name` SHALL 為 null，其他欄位 SHALL NOT 變動

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

家屬 SHALL 由 `FamilyAuthorizationService.notification_recipients(就診者, "appointment_reminder")` 決定；T-1h、T+0、T+30 SHALL 使用同一份名單。強制模式下合格角色為 GUARDIAN 與 CAREGIVER；影子模式下為族譜全員。本人 SHALL NOT 出現在家屬名單中。

每位收件人 SHALL 依自己的設定：本人看 `notify_reminder`，家屬看 `notify_family`；語言與字級取收件人自己的設定。

### Requirement: 出發與到診

本人 SHALL 可回報；對就診者有 GENERAL 寫入權者 SHALL 可代為回報。實際按下的人 SHALL 寫入 `departed_by_user_id`／`attended_by_user_id`。

`depart` SHALL 只接受 `scheduled`；`attend` SHALL 接受 `scheduled` 或 `departed`。已是目標狀態時 SHALL 冪等回傳（200），SHALL NOT 覆寫原本的回報者；`attended`／`missed`／`cancelled` 不是合法來源時 SHALL 回 409。門診當地日 00:00 與 T-1h 兩者較早者之前 SHALL 回 409。

LIFF 的 `POST .../depart`、`POST .../attend` 與 LINE 卡片的 postback SHALL 呼叫同一個服務方法。

### Requirement: 推播的隱私邊界

任何推播（含 altText）SHALL NOT 含科別、醫師、看診號或備註。推播 SHALL 只含日期時間、醫院名稱，家屬版另含就診者的顯示名稱。

#### Scenario: 精神科門診

- **WHEN** 一筆科別為「精神科」的提醒走完三個推播階段
- **THEN** 本人與家屬收到的每一則訊息 SHALL NOT 出現「精神科」

### Requirement: 推播時間點由後端提供

每筆回應 SHALL 附 `notify_at`：還會推播時為 `[T-1h, T+0, T+30]`（以 `appointment_at` 的 offset 表示），不會再推播（`enabled=false` 或狀態為 `attended`／`missed`／`cancelled`）時為空陣列。
