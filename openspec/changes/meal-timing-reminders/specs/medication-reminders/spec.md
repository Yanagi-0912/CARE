## ADDED Requirements

### Requirement: 規則內的服藥條目

每筆時段規則 SHALL 含 `entries` 陣列，每個條目為 `(meal_timing, scheduled_time, medication_ids)`。`meal_timing` SHALL 限定 `before_meal`、`after_meal`、`none` 三種；同一規則內每種 SHALL 至多一個條目，且 SHALL 至少有一個條目。條目的 `scheduled_time` SHALL 為 24 小時制 `HH:MM`，於 API 層驗證。

規則的 `scheduled_time` SHALL 等於條目中最早的時刻，`timeout_anchor_time` SHALL 等於條目中最晚的時刻，`medication_ids` SHALL 等於各條目 `medication_ids` 的聯集。三者 SHALL 由儲存層於每次寫入前重算，SHALL NOT 由 API 直接寫入。

「同一位用藥者的同一個時段只有一筆規則」的不變量 SHALL 維持不變；飯前與飯後 SHALL NOT 拆成兩筆規則。

既有規則於資料庫中沒有 `entries` 時，讀回 SHALL 合成單一 `none` 條目，其時刻與藥品取自規則既有的 `scheduled_time` 與 `medication_ids`；`timeout_anchor_time` 缺席時 SHALL 視為等於 `scheduled_time`。其排程、推播與確認行為 SHALL 與本變更前完全一致。

#### Scenario: 早上飯前飯後各一批藥

- **WHEN** 使用者為「早」設定飯前 07:30（降血糖藥）與飯後 08:30（血壓藥、胃藥）
- **THEN** 系統 SHALL 建立一筆 `morning` 規則，`entries` 含兩個條目，`scheduled_time` 為 07:30，`timeout_anchor_time` 為 08:30，`medication_ids` 含三種藥

#### Scenario: 既有規則沒有條目欄位

- **WHEN** 讀取一筆本變更前建立的規則
- **THEN** 其 `entries` SHALL 為單一 `none` 條目，時刻等於 `scheduled_time`，該規則 SHALL 照常展開與推播

#### Scenario: 同種時機重複

- **WHEN** 請求的條目含兩個 `before_meal`
- **THEN** 系統 SHALL 回傳驗證錯誤，SHALL NOT 寫入

### Requirement: 逐藥確認

推播給用藥者的服藥提醒與二次催促，其藥品清單的每一列 SHALL 附一顆確認按鈕，postback 帶該紀錄 id 與該藥品 id。按下時系統 SHALL 將該藥品 id 加入紀錄的 `taken_medication_ids`（集合語意，重複按下 SHALL 冪等）。

當規則於紀錄當日有效的藥品集合皆已在 `taken_medication_ids` 中時，紀錄狀態 SHALL 轉為 `taken` 並記錄 `taken_at`；未到齊時狀態 SHALL 維持不變。判定所用的藥品集合 SHALL 於確認當下依規則現況計算，SHALL NOT 使用推播當時的快照。

逐藥確認的回應 SHALL 以 reply token 回覆：未到齊時回覆一則列出已記錄藥品與尚未確認藥品的純文字訊息；到齊時回覆現有的完成卡片。回覆 SHALL NOT 消耗推播額度。

不帶藥品 id 的確認（【全部已服用】）SHALL 維持既有行為：紀錄直接轉為 `taken`，並將當日有效的藥品全部寫入 `taken_medication_ids`。逐藥確認與整批確認皆 SHALL 限該紀錄的用藥者本人。

`medication_ids` 為空的規則 SHALL NOT 出現逐藥按鈕，版面 SHALL 與本變更前相同。超過顯示上限而收斂為計數的藥品 SHALL NOT 有按鈕。

#### Scenario: 三種藥逐一確認

- **WHEN** 某時段有三種有效藥品，用藥者依序按下其中兩顆的確認
- **THEN** 紀錄狀態 SHALL 維持 `pending`，每次 SHALL 回覆列出剩餘藥品的訊息；按下第三顆後狀態 SHALL 為 `taken` 並回覆完成卡

#### Scenario: 重複按同一顆

- **WHEN** 用藥者對同一顆藥按下確認兩次
- **THEN** `taken_medication_ids` SHALL 只含該藥一次，第二次 SHALL 回覆與第一次相同的訊息

#### Scenario: 訊息送出後藥品被停用

- **WHEN** 推播列出 A、B 兩種藥，之後 B 被停用，用藥者只按下 A
- **THEN** 紀錄狀態 SHALL 為 `taken`

#### Scenario: 整批確認

- **WHEN** 用藥者按下【全部已服用】
- **THEN** 紀錄狀態 SHALL 為 `taken`，`taken_medication_ids` SHALL 含當日全部有效藥品

### Requirement: 藥品的列出與手動新增

系統 SHALL 提供列出某位用藥者全部藥品的端點（含已停用者並標示 `enabled`），以及以藥名手動新增藥品的端點（`source` 為 `manual`，不含藥證、外觀與適應症）。兩者的授權 SHALL 與提醒規則的讀／寫授權相同。

手動新增的藥品 SHALL NOT 觸發藥證比對或成分警示；其縮圖與仿單欄位 SHALL 為空。

#### Scenario: 家屬為長輩手動加藥

- **WHEN** 具寫入權的家屬以藥名「胃藥」為長輩新增藥品
- **THEN** 系統 SHALL 建立 `source=manual` 的藥品，`user_id` 為長輩、`created_by_user_id` 為家屬

#### Scenario: 無權者列出藥品

- **WHEN** 對該用藥者無讀取權的使用者呼叫列出端點
- **THEN** 系統 SHALL 拒絕

## MODIFIED Requirements

### Requirement: 提醒規則與用藥對象

系統 SHALL 將用藥提醒保存為循環規則，每筆規則含開立者（`creator_user_id`）與用藥者（`user_id`）兩個獨立欄位，兩者可為不同人。時段限定為 `morning`、`noon`、`evening`、`bedtime` 四種，預設時間分別為 08:00、12:00、18:00、21:30，可逐時段覆寫；亦可逐時段給定服藥條目（`slot_entries`），有給定者 SHALL 以條目為準。

建立請求指定的時段若該用藥者已有規則，系統 SHALL 回傳 409，SHALL NOT 建立第二筆。

為他人建立或查詢提醒時，授權 SHALL 依家庭授權矩陣判定；修改與刪除亦同。

#### Scenario: 為家庭成員建立提醒

- **WHEN** 使用者為有寫入權的成員勾選早、晚兩個時段
- **THEN** 系統建立兩筆規則，`creator_user_id` 為操作者、`user_id` 為該成員

#### Scenario: 時段已有規則

- **WHEN** 使用者為已有「早」規則的用藥者再次建立「早」
- **THEN** 系統 SHALL 回傳 409，SHALL NOT 建立任何規則

#### Scenario: 對象無權

- **WHEN** 使用者對指定的 `user_id` 不具寫入權
- **THEN** 系統 SHALL 拒絕並回傳錯誤，SHALL NOT 建立任何規則

### Requirement: 提醒時間格式驗證

`scheduled_time`、`slot_times` 與各條目的 `scheduled_time` SHALL 為 24 小時制 `HH:MM`，於 API 層以驗證器拒絕不合格式者。

更新請求對含多個條目的規則帶 `scheduled_time` 時，系統 SHALL 回傳 400——多個條目各有時刻，單一時間無從對應；該情況 SHALL 改以 `entries` 整份更新。

理由：排程器以 `strptime` 解析該欄位，格式錯誤會拋出例外並被逐筆的 try/except 吞掉——該筆提醒將永遠不會觸發，且使用者收不到任何錯誤回饋。

#### Scenario: 非法時間格式

- **WHEN** 建立請求帶入 `{"morning": "9am"}`
- **THEN** 系統 SHALL 回傳驗證錯誤，SHALL NOT 寫入資料庫

#### Scenario: 多條目規則只改單一時間

- **WHEN** 對含飯前、飯後兩個條目的規則送出僅帶 `scheduled_time` 的更新
- **THEN** 系統 SHALL 回傳 400

### Requirement: 三階遞進推播

對展開後仍為 `pending` 的紀錄，系統 SHALL 依下列時序推播：

1. **T+0**：於規則 `scheduled_time`（最早條目時刻）推播服藥提醒給用藥者，附逐藥確認按鈕與【全部已服用】按鈕
2. **T+20 分鐘**：以 `timeout_anchor_time`（最晚條目時刻）起算 20 分鐘，仍未確認時推播二次催促給用藥者，只列尚未確認的藥品
3. **T+30 分鐘**：以 `timeout_anchor_time` 起算 30 分鐘，仍未確認時推播逾時警報給通報對象（`alert_notify_user_id`），並將狀態改為 `missed`

紀錄 SHALL 於展開時寫入 `urgent_at` 與 `timeout_at`；T+20 的待推播查詢 SHALL 讀 `urgent_at`，`urgent_at` 缺席的既有紀錄 SHALL 退回 `scheduled_at + 20 分鐘`。單一 `none` 條目時三階時序 SHALL 與本變更前相同。

各階段的到期判定 SHALL 使用「小於等於」而非精確相等。各階段 SHALL 各自維護已送出旗標，確保同一則訊息不重複發送。

#### Scenario: 飯前飯後時刻不同

- **WHEN** 規則的飯前 07:30、飯後 08:30，用藥者未做任何確認
- **THEN** 07:30 SHALL 推播提醒，08:50 SHALL 推播催促，09:00 通報對象 SHALL 收到逾時警報；08:00 前後 SHALL NOT 有任何催促或警報

#### Scenario: 逾時未確認

- **WHEN** 用藥者於 `timeout_anchor_time` 後 30 分鐘仍未全部確認
- **THEN** 通報對象 SHALL 收到逾時警報，該紀錄狀態 SHALL 為 `missed`

#### Scenario: 中途完成確認

- **WHEN** 用藥者於催促前完成全部確認
- **THEN** SHALL NOT 再推播二次催促與逾時警報

### Requirement: 改排程後當日紀錄的對齊

更新一筆時段規則且其 `slot_type`、`scheduled_time`（最早條目時刻）或 `timeout_anchor_time`（最晚條目時刻）實際改變時，系統 SHALL 將該規則當日已展開、狀態仍為 `pending` 的紀錄對齊新的排程：

- `scheduled_at` 與新的最早時刻**不同**者 SHALL 改為 `cancelled`。新時刻由排程器照常展開為另一筆紀錄。
- `scheduled_at` 與新的最早時刻**相同**者 SHALL 就地改寫：`slot_type` 改變時改寫 `slot_type`；最晚時刻改變時改寫 `urgent_at` 與 `timeout_at`。SHALL NOT 註銷。

只改動條目的藥品指派而各時刻皆未變者，SHALL NOT 改寫或註銷任何紀錄——推播與確認判定皆於當下讀取規則。

其餘條文（僅作用於 `pending`；原值重送不算改變；`enabled=false` 走關閉路徑；改到已超過補推期限的時刻預先寫入 `cancelled`；時刻計算以台北時間當日為準）維持不變。

#### Scenario: 改時段後舊時刻不再催促

- **WHEN** 08:00 的「早」紀錄已展開且尚未確認，使用者於 08:05 將該規則改為「中」12:00
- **THEN** 該紀錄狀態 SHALL 為 `cancelled`，SHALL NOT 於 08:20 催促，家屬 SHALL NOT 於 08:30 收到逾時警報；系統 SHALL 於 12:00 展開新的紀錄照常提醒

#### Scenario: 只把飯後時間往後移

- **WHEN** 規則飯前 07:30、飯後 08:30 的紀錄已於 07:30 展開且仍為 `pending`，使用者於 07:40 將飯後改為 09:00
- **THEN** 該紀錄 SHALL 維持 `pending`，`urgent_at` SHALL 改為 09:20、`timeout_at` SHALL 改為 09:30

#### Scenario: 只改藥品指派

- **WHEN** 使用者把某顆藥從飯後移到飯前，兩個時刻皆未變
- **THEN** SHALL NOT 註銷或改寫任何當日紀錄

#### Scenario: 時刻未變只換時段名稱

- **WHEN** 使用者將自訂於 07:15 的「早」規則改為「中」，時間維持 07:15，且當日 07:15 的紀錄仍為 `pending`
- **THEN** 該紀錄的 `slot_type` SHALL 改為 `noon`，狀態 SHALL 維持 `pending`

#### Scenario: 已確認的紀錄不受改排程影響

- **WHEN** 使用者已完成確認後才更改該規則的時間
- **THEN** 該紀錄狀態 SHALL 維持 `taken`

### Requirement: 服藥確認

用藥者完成確認（逐藥到齊或按下【全部已服用】）時，系統 SHALL 將該紀錄狀態更新為 `taken` 並記錄確認時間，且 SHALL 以完成卡片回覆。狀態為 `pending`、`missed` 或 `cancelled` 者皆 SHALL 允許被確認；已為 `taken` 者 SHALL 視為冪等成功。

確認 SHALL 限該紀錄的用藥者本人。

#### Scenario: 逾時後才確認

- **WHEN** 家屬已收到逾時警報（狀態為 `missed`）後，用藥者才完成確認
- **THEN** 狀態 SHALL 更新為 `taken`

#### Scenario: 非本人確認

- **WHEN** 非該紀錄用藥者的使用者呼叫確認
- **THEN** 系統 SHALL 拒絕

### Requirement: 提醒規則關聯藥品

`MedicationReminder` 的 `medication_ids` SHALL 為各條目 `medication_ids` 的聯集，唯讀；藥品 SHALL 掛在條目上。

藥袋辨識提交連結藥品時，SHALL 掛到該規則的 `none` 條目；該條目缺席時 SHALL 以規則現行 `scheduled_time` 建立之。

既有規則於資料庫中沒有此欄位，讀回時 SHALL 視為空陣列，其排程與推播行為 SHALL 與本變更前完全一致。

藥品關聯 SHALL 僅是關聯，SHALL NOT 影響排程器展開執行紀錄的判定。排程器 SHALL 維持只依 `slot_type`、`scheduled_time`、`start_date`、`end_date`、`enabled` 與規則 `created_at` 決定是否展開。

同一個藥品 MAY 同時關聯至同一位用藥者的多個時段規則；同一規則內 SHALL NOT 同時掛在兩個條目。條目指定的藥品 SHALL 全部屬於該用藥者，否則 SHALL 回傳 400。

#### Scenario: 既有規則無藥品欄位

- **WHEN** 排程器讀取一筆本變更前建立、資料庫中沒有 `medication_ids` 的規則
- **THEN** 該規則 SHALL 正常展開與推播，`medication_ids` SHALL 視為空陣列

#### Scenario: 藥袋提交掛到未指定時機的條目

- **WHEN** 「早」規則已有飯前、飯後兩個條目，藥袋辨識提交一顆早上的藥
- **THEN** 該藥 SHALL 掛在新建立的 `none` 條目，時刻等於規則的 `scheduled_time`；飯前、飯後條目 SHALL NOT 改變

#### Scenario: 指派他人的藥

- **WHEN** 條目的 `medication_ids` 含不屬於該用藥者的藥品 id
- **THEN** 系統 SHALL 回傳 400，SHALL NOT 寫入

### Requirement: 推播列出該時段應服藥品

推播給用藥者的服藥提醒與二次催促 SHALL 依「飯前 → 飯後 → 其他」的固定順序分區列出各條目當下有效的藥品；每區 SHALL 標示時機名稱與該條目的時刻。只有 `none` 一個條目時 SHALL NOT 顯示分區標題，版面除逐藥按鈕外 SHALL 與本變更前相同。時機名稱 SHALL 依收件人語言呈現。

二次催促 SHALL 只列尚未確認的藥品；全部條目皆無待列藥品時 SHALL 維持既有版面。

藥品名稱與縮圖以外的欄位 SHALL NOT 出現在推播中；適應症尤其 SHALL NOT 出現。`medication_ids` 為空、或其對應藥品皆已失效時，推播 SHALL 維持既有版面。超過顯示上限的藥品 SHALL 收斂為單行計數。

家屬的 T+30 逾時警報 SHALL 列出該時段尚未確認服用的藥品名稱，讓建立提醒的家屬（`alert_notify_user_id` 取自 `reminder.creator_user_id`）判斷漏掉的是哪一種藥；SHALL NOT 呈現飯前／飯後等服藥時機字樣、藥丸縮圖或適應症。系統中斷期間的錯過時段彙整通知（`build_caregiver_missed_summary_flex`）性質不同——它一次彙整多個時段、可能橫跨多位家人，SHALL NOT 列出藥品名稱，僅維持既有的病患姓名、時段與時刻措辭。

#### Scenario: 飯前飯後分區

- **WHEN** 「早」規則飯前一種藥、飯後兩種藥且觸發服藥提醒
- **THEN** 推播 SHALL 先列「飯前 07:30」區塊含一種藥，再列「飯後 08:30」區塊含兩種藥，每列各附確認按鈕

#### Scenario: 催促只列剩餘

- **WHEN** 用藥者已確認飯前那顆藥，T+20 觸發催促
- **THEN** 催促 SHALL 只列飯後的兩種藥

#### Scenario: 手動建立的規則

- **WHEN** 某規則的 `medication_ids` 為空且觸發服藥提醒
- **THEN** 推播版面 SHALL 與本變更前相同

#### Scenario: 家屬警報列出漏掉的藥品但不含服藥時機字樣

- **WHEN** 逾時警報推播給家屬
- **THEN** 訊息 SHALL 列出該時段尚未確認的藥品名稱，SHALL NOT 含「飯前」「飯後」等服藥時機字樣

#### Scenario: 錯過時段彙整通知不列藥品

- **WHEN** 系統中斷期間錯過的時段彙整成通知推播給家屬
- **THEN** 訊息 SHALL NOT 含任何藥品名稱
