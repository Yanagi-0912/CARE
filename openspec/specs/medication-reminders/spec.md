# Medication Reminders Spec

## Purpose

定義 CARE 用藥提醒的規則管理、每日排程展開、三階遞進推播，以及排程器在停機與多實例情境下的行為。實作位於 `app/services/medication/`（`medication_service.py`、`medication_scheduler.py`）、`app/repositories/medication_repository.py`、`app/routers/users/medications.py`、`app/models/medication.py`、`app/services/line_messaging/flex/medication_flex.py` 與 `app/services/line_messaging/dispatcher/dispatcher.py`（逐藥確認的 postback 處理）。
## Requirements
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

### Requirement: 每日執行紀錄的展開

排程器 SHALL 每次 tick 依當日已到期且啟用中的規則，惰性展開為當日的執行紀錄（log），SHALL NOT 預先產生整天或未來日期的紀錄。展開 SHALL 以 `(reminder_id, scheduled_at)` 為唯一識別做 upsert，且僅在插入時寫入初始欄位，既有紀錄的狀態 SHALL NOT 被覆寫。

規則的 `start_date` 與 `end_date` SHALL 納入當日是否展開的判定；`end_date` 為空代表長期提醒。

排程器 SHALL NOT 為早於該規則 `created_at` 的時段展開紀錄。理由：20:00 新增一筆 08:00 的提醒時，當日 08:00 已成過去，補建會在同一個 tick 內連續觸發三個階段的推播，而使用者從未錯過任何一次提醒。

#### Scenario: 新增提醒後不補當日較早的時段

- **WHEN** 使用者於 20:00 新增一筆 08:00 的提醒
- **THEN** 系統 SHALL NOT 為當日 08:00 建立紀錄

#### Scenario: 提醒已逾結束日期

- **WHEN** 規則的 `end_date` 早於今日
- **THEN** 系統 SHALL NOT 為今日展開紀錄

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

### Requirement: 推播權原子搶佔

推播前，系統 SHALL 以「該階段的已送出旗標仍為 false」為條件，對單一紀錄做原子更新以取得推播權；未取得者 SHALL NOT 推播，且 SHALL NOT 還原任何旗標。推播失敗（回傳失敗或拋出例外）時，取得推播權者 SHALL 還原該旗標，交由後續 tick 重試。

理由：「查詢待推播 → 呼叫 LINE API → 標記已送出」之間沒有原子性。只要同時有兩個實例在跑排程，兩邊會查到同一筆未送出的紀錄並各推一次。後端 Deployment 的滾動更新策略為 `maxUnavailable: 0` 搭配 `maxSurge: 1`，代表舊 Pod 必須等新 Pod Ready 之後才終止——每次部署都保證存在新舊並存的時間窗，而排程器於 lifespan startup 即啟動並立刻執行一次 tick。

還原家屬逾時警報的旗標時，狀態 SHALL 僅在仍為 `missed` 的情況下回寫 `pending`；使用者可能在推播失敗的空檔完成確認，該情況下狀態為 `taken`，SHALL NOT 被還原動作覆寫。

#### Scenario: 兩個實例同時查到同一筆

- **WHEN** 滾動更新期間新舊 Pod 各自查到同一筆未送出的紀錄
- **THEN** 僅其中一個取得推播權並送出，使用者 SHALL 只收到一則

#### Scenario: 推播失敗

- **WHEN** 取得推播權後 LINE API 回傳失敗
- **THEN** 系統 SHALL 還原該階段旗標，下一個 tick SHALL 重新嘗試

### Requirement: 執行紀錄唯一性

`medication_logs` SHALL 於 `(reminder_id, scheduled_at)` 建立唯一索引，並於應用啟動時確保其存在。upsert 因唯一索引而被拒時，SHALL 視為「該紀錄已存在」處理，SHALL NOT 中斷該次 tick。

理由：`$setOnInsert` 只保證不覆寫既有欄位，不保證併發時只插入一筆。缺少唯一索引時，兩個實例可各插入一份紀錄，兩份紀錄各自被搶佔、各自推播，推播權搶佔即形同虛設。

既有資料存在重複組合而導致索引無法建立時，系統 SHALL 記錄錯誤，SHALL NOT 因此讓應用啟動失敗。

#### Scenario: 併發插入同一時段

- **WHEN** 兩個實例同時為同一個 `(reminder_id, scheduled_at)` 執行 upsert
- **THEN** 資料庫中 SHALL 僅存在一份紀錄

### Requirement: 錯過時段不補推播

展開紀錄時，若該時段已早於當下超過 misfire grace（預設 20 分鐘），系統 SHALL 建立紀錄並直接記為 `missed`、三個階段旗標全部設為已送出，SHALL NOT 推播該時段的任何一階訊息。

理由：規則 `created_at` 的檢查只擋得住「提醒是後來才建立的」，擋不住「服務當時沒在跑」。服務於停機後啟動時，當日所有已到期時段會在同一個 tick 內被展開，接著三個階段依序判定成立——使用者一次收到多則提醒與催促，家屬同時收到多則逾時警報。

grace 之內的延遲 SHALL 照常推播，使短暫部署造成的延遲不致漏發。

#### Scenario: 停機後啟動

- **WHEN** 服務於 07:00 至 15:00 停機，15:00 執行第一個 tick，當日有 08:00 與 12:00 兩個時段
- **THEN** 兩筆紀錄 SHALL 建立且狀態為 `missed`，用藥者與家屬 SHALL NOT 收到該兩個時段的三階推播

#### Scenario: grace 之內的延遲

- **WHEN** 08:00 的時段延遲至 08:15 才展開
- **THEN** 紀錄狀態 SHALL 為 `pending` 並照常推播首刷提醒

### Requirement: 錯過時段的彙整通知

系統 SHALL 於首次發現錯過時段時，依通報對象彙整成一則通知送出，每位通報對象每次 tick SHALL 至多收到一則。通知內容 SHALL 依用藥者分組列出錯過的時段與時間。

判定「首次發現」SHALL 依據 upsert 是否實際插入紀錄，SHALL NOT 僅依據該時段是否早於 grace——後者每個 tick 都會重新成立，會使通知每分鐘重複發送。

通知措辭 SHALL 與 T+30 逾時警報區隔：逾時警報陳述「家人逾時未服藥」，本通知陳述「系統中斷期間未能發出提醒，因此無法確認是否服藥」。沿用逾時警報的措辭會使家屬誤判為長輩未服藥。

超出顯示上限的時段 SHALL 收斂為單行計數，避免長時間停機產生過大的訊息。

本通知為中斷後的補充告知，送出失敗時 SHALL 僅記錄錯誤，SHALL NOT 重試——錯過的時段本身已以 `status=missed` 留存於資料庫。

#### Scenario: 停機期間錯過多個時段

- **WHEN** 同一位家屬照顧的成員有 08:00 與 12:00 兩個時段因停機而錯過
- **THEN** 該家屬 SHALL 收到一則列出兩個時段的通知，SHALL NOT 收到兩則

#### Scenario: 後續 tick 不重複通知

- **WHEN** 錯過時段的紀錄已於先前的 tick 建立
- **THEN** SHALL NOT 再次發送彙整通知

### Requirement: 服藥確認

用藥者完成確認（逐藥到齊或按下【全部已服用】）時，系統 SHALL 將該紀錄狀態更新為 `taken` 並記錄確認時間，且 SHALL 以完成卡片回覆。狀態為 `pending`、`missed` 或 `cancelled` 者皆 SHALL 允許被確認；已為 `taken` 者 SHALL 視為冪等成功。

確認 SHALL 限該紀錄的用藥者本人。

#### Scenario: 逾時後才確認

- **WHEN** 家屬已收到逾時警報（狀態為 `missed`）後，用藥者才完成確認
- **THEN** 狀態 SHALL 更新為 `taken`

#### Scenario: 非本人確認

- **WHEN** 非該紀錄用藥者的使用者呼叫確認
- **THEN** 系統 SHALL 拒絕

### Requirement: 關閉時段規則

關閉一筆時段規則（`enabled` 設為 false）SHALL 立即止住該規則當日尚未確認的紀錄的後續推播：系統 SHALL 將其狀態由 `pending` 改為 `cancelled`。

`cancelled` SHALL NOT 計為漏吃——該紀錄 SHALL NOT 觸發 T+20 二次催促、SHALL NOT 觸發 T+30 家屬逾時警報，狀態 SHALL NOT 變為 `missed`。

理由：三階推播的待推播查詢只讀執行紀錄，不回頭確認規則現在是否仍啟用。若僅寫入 `enabled=false`，當日已展開的紀錄會照常走完催促與家屬警報——使用者主動關閉後仍被催促，家屬還收到他漏服藥的警報，關閉因此看起來完全沒有作用。

三階推播的查詢條件 SHALL 維持限定 `status` 為 `pending`。此為註銷得以生效的唯一依據：條件一旦放寬（例如改為排除 `taken`），關閉將再次悄悄失效。

註銷 SHALL 僅作用於 `pending` 的紀錄。已為 `taken` 者 SHALL NOT 被改寫——那是使用者確實服藥的事實；已為 `missed` 者亦 SHALL NOT 被改寫——家屬警報已送出，事後改為「不算漏吃」會使資料庫與已送達的通知互相矛盾。

註銷 SHALL 於規則更新成功之後才執行；更新失敗時規則仍為啟用，SHALL NOT 作廢當日紀錄。未帶 `enabled` 的更新請求 SHALL NOT 走本節的全面註銷；該請求若改動了排程，當日紀錄的處置見「改排程後當日紀錄的對齊」——那是逐筆對齊，不是把這筆規則今天的紀錄一併作廢。

關閉 SHALL NOT 刪除規則本身，亦 SHALL NOT 停用其關聯藥品。同日再次開啟 SHALL NOT 復原已註銷的紀錄——展開以 `(reminder_id, scheduled_at)` 為唯一識別且僅在插入時寫入初始欄位，已存在的紀錄不會被改回 `pending`，該時段當日因此不再推播。

`cancelled` SHALL NOT 為終局狀態：使用者對已註銷的紀錄按下【已用藥】，系統 SHALL 將其狀態改為 `taken`。理由：使用者可能先服了藥才關閉該時段（例如療程結束），最後才按下推播訊息上仍留著的確認；服藥是事實，紀錄應收斂為 `taken`。此與 `missed` 允許事後轉 `taken` 為同一判斷——使用者按下的確認一律優先於系統推得的狀態。此放寬 SHALL NOT 使推播復活：三階查詢限定 `pending`，`taken` 同樣不會被挑中。

用藥歷史 SHALL NOT 列出狀態為 `cancelled` 的紀錄。理由：該狀態是為阻止排程器於同日後續 tick 重新展開而留下的內部記帳，並非使用者的行為；列出會使使用者在歷史中看到一筆自己從未互動、狀態亦無從解讀的紀錄。已由 `cancelled` 轉為 `taken` 者 SHALL 照常列出。

#### Scenario: 關閉後不再催促

- **WHEN** 08:00 的紀錄已展開且尚未確認，使用者於 08:05 關閉該時段規則
- **THEN** 該紀錄狀態 SHALL 為 `cancelled`，用藥者 SHALL NOT 收到 T+20 催促，家屬 SHALL NOT 收到 T+30 逾時警報

#### Scenario: 已確認的紀錄不受關閉影響

- **WHEN** 使用者已按下【已用藥】後才關閉該時段規則
- **THEN** 該紀錄狀態 SHALL 維持 `taken`

#### Scenario: 只調整提醒時間不走全面註銷

- **WHEN** 更新請求僅帶 `scheduled_time`，未帶 `enabled`
- **THEN** SHALL NOT 對該規則執行全面註銷；當日紀錄僅依「改排程後當日紀錄的對齊」逐筆處置

#### Scenario: 關閉後才補按已用藥

- **WHEN** 該時段的紀錄已為 `cancelled`，使用者按下【已用藥】
- **THEN** 該紀錄狀態 SHALL 為 `taken`，且 SHALL 出現在用藥歷史中

#### Scenario: 已註銷的紀錄不進歷史

- **WHEN** 查詢使用者的用藥歷史，其中一筆紀錄狀態為 `cancelled`
- **THEN** 回傳結果 SHALL NOT 包含該筆紀錄

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

### Requirement: 推播的時區與顯示設定

推播文案中的時間 SHALL 以台北時間顯示。從資料庫取回的時間為無時區的 UTC，SHALL 先補上時區再轉換，否則會顯示為相差 8 小時的時刻。

每則推播的語言與字級 SHALL 依**收件人本人**的 `settings` 解析：送給用藥者的訊息取用藥者的設定，送給家屬的警報與彙整通知取家屬的設定。設定缺漏或不在支援語系集合內時 SHALL 回退為預設值。

#### Scenario: 家屬與用藥者語言不同

- **WHEN** 用藥者語言為 `zh-TW`、家屬語言為 `en`
- **THEN** 逾時警報 SHALL 以 `en` 呈現

#### Scenario: 資料庫時間的轉換

- **WHEN** `scheduled_at` 自資料庫讀回為無時區的 UTC 00:00
- **THEN** 推播文案 SHALL 顯示 08:00

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

### Requirement: 推播的藥品清單得帶出藥丸縮圖

給用藥者的服藥提醒與二次催促，其藥品清單的每一列 SHALL 在該藥品的 `license_number` 已確定且有可用照片時，於藥名旁呈現藥丸縮圖。

沒有照片的藥品 SHALL 維持純文字列，且同一清單中圖文混排 SHALL NOT 使版面破損。`medication_ids` 為空時的版面 SHALL 與本變更前完全相同。

藥品數量仍 SHALL 受既有顯示上限收斂為單行計數。

家屬的逾時警報與錯過時段彙整通知 SHALL NOT 呈現藥丸縮圖——逾時警報雖然列出藥品名稱（見「推播列出該時段應服藥品」），但收件人是當初替家人建立這些藥的人，藥名已足以判斷漏掉的是哪一種，縮圖只是外觀，不會提供額外判斷依據，反而讓通知列多暴露一份病情相關的視覺資訊；彙整通知則連藥品名稱都不列，更沒有理由呈現縮圖。

#### Scenario: 有照片的藥品

- **WHEN** 某時段的藥品證號已確定且有可用照片
- **THEN** 該列 SHALL 呈現縮圖與藥名

#### Scenario: 同時段圖文混排

- **WHEN** 某時段同時有帶照片與無照片的藥品
- **THEN** 無照片者 SHALL 呈現為純文字列，版面 SHALL NOT 破損

#### Scenario: 家屬卡片不含縮圖

- **WHEN** 逾時警報推播給家屬
- **THEN** 訊息 SHALL NOT 含任何藥丸縮圖

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

