## ADDED Requirements

### Requirement: 提醒規則關聯藥品

`MedicationReminder` SHALL 新增 `medication_ids` 欄位，記錄該時段應服用的藥品。欄位預設為空陣列。

既有規則於資料庫中沒有此欄位，讀回時 SHALL 視為空陣列，其排程與推播行為 SHALL 與本變更前完全一致。

`medication_ids` SHALL 僅是關聯，SHALL NOT 影響排程器展開執行紀錄的判定。排程器 SHALL 維持只依 `slot_type`、`scheduled_time`、`start_date`、`end_date`、`enabled` 與規則 `created_at` 決定是否展開。

理由：排程器的展開、原子搶佔與停機補償行為已有既定條文與併發保證，把藥品關聯排除在展開判定之外，可讓本次變更不必重新驗證那些併發行為。

同一個藥品 MAY 同時關聯至同一位用藥者的多個時段規則。

#### Scenario: 既有規則無藥品欄位

- **WHEN** 排程器讀取一筆本變更前建立、資料庫中沒有 `medication_ids` 的規則
- **THEN** 該規則 SHALL 正常展開與推播，`medication_ids` SHALL 視為空陣列

#### Scenario: 一種藥關聯多個時段

- **WHEN** 某藥品需一日三次
- **THEN** 該藥品的 id SHALL 出現在該用藥者 `morning`、`noon`、`evening` 三筆規則的 `medication_ids` 中

### Requirement: 推播列出該時段應服藥品

推播給用藥者的服藥提醒與二次催促 SHALL 列出該規則 `medication_ids` 所對應、且當下有效的藥品名稱。

藥品名稱以外的欄位 SHALL NOT 出現在推播中；適應症尤其 SHALL NOT 出現。

`medication_ids` 為空、或其對應藥品皆已失效時，推播 SHALL 維持既有版面，SHALL NOT 呈現空白的藥品區塊。

超過顯示上限的藥品 SHALL 收斂為單行計數，避免藥品數量過多時產生過長的訊息。

家屬的逾時警報與錯過時段彙整通知 SHALL NOT 列出藥品名稱，SHALL 維持既有措辭。

理由：警報與彙整通知的收件人是家屬而非用藥者，其目的是「有沒有吃」而非「該吃什麼」；列出藥品名稱只會擴大病情資訊在通知列的暴露面。

#### Scenario: 時段有兩種藥

- **WHEN** 某時段的規則關聯兩個有效藥品且觸發服藥提醒
- **THEN** 推播 SHALL 列出這兩個藥品的名稱

#### Scenario: 手動建立的規則

- **WHEN** 某規則的 `medication_ids` 為空且觸發服藥提醒
- **THEN** 推播版面 SHALL 與本變更前相同

#### Scenario: 家屬警報不列藥品

- **WHEN** 逾時警報推播給家屬
- **THEN** 訊息 SHALL NOT 含任何藥品名稱

### Requirement: 藥品的有效性獨立於時段規則

藥品 SHALL 有自己的 `enabled` 與療程起訖日期。藥品被停用、或當日不在其療程區間內時，該藥品 SHALL 視為當下無效，SHALL NOT 出現在推播的藥品清單中。

停用或結束一個藥品 SHALL NOT 停用任何時段規則——同一時段可能還有其他藥要吃。

刪除一筆時段規則 SHALL NOT 刪除其關聯的藥品；藥品 SHALL 獨立存在，並可被重新關聯至其他規則。

當某時段規則的所有關聯藥品都已失效時，該規則 SHALL 維持啟用並照常推播，SHALL NOT 自動停用。理由：規則可能是使用者手動建立的，自動停用會靜默移除他明確設定過的提醒。

#### Scenario: 療程結束

- **WHEN** 某藥品的療程結束日期早於今日
- **THEN** 該藥品 SHALL NOT 出現在當日推播的藥品清單中，該時段規則 SHALL 維持啟用

#### Scenario: 停用單一藥品

- **WHEN** 使用者停用某時段兩種藥中的一種
- **THEN** 該時段 SHALL 照常推播，藥品清單 SHALL 僅列出另一種
