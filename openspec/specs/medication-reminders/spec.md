# Medication Reminders Spec

## Purpose

定義 CARE 用藥提醒的規則管理、每日排程展開、三階遞進推播，以及排程器在停機與多實例情境下的行為。實作位於 `app/services/medication/`（`medication_service.py`、`medication_scheduler.py`）、`app/repositories/medication_repository.py`、`app/routers/users/medications.py`、`app/models/medication.py` 與 `app/services/line_messaging/flex/medication_flex.py`。
## Requirements
### Requirement: 提醒規則與用藥對象

系統 SHALL 將用藥提醒保存為循環規則，每筆規則含開立者（`creator_user_id`）與用藥者（`user_id`）兩個獨立欄位，兩者可為不同人。時段限定為 `morning`、`noon`、`evening`、`bedtime` 四種，預設時間分別為 08:00、12:00、18:00、21:30，可逐時段覆寫。

為他人建立或查詢提醒時，該對象 SHALL 為開立者家庭族譜內的成員；否則 SHALL 拒絕。修改與刪除 SHALL 限開立者本人或用藥者本人。

#### Scenario: 為家庭成員建立提醒

- **WHEN** 使用者為族譜內的成員勾選早、晚兩個時段
- **THEN** 系統建立兩筆規則，`creator_user_id` 為操作者、`user_id` 為該成員

#### Scenario: 對象不在族譜內

- **WHEN** 使用者指定的 `user_id` 不在其家庭族譜中
- **THEN** 系統 SHALL 拒絕並回傳錯誤，SHALL NOT 建立任何規則

### Requirement: 提醒時間格式驗證

`scheduled_time` 與 `slot_times` 的值 SHALL 為 24 小時制 `HH:MM`，於 API 層以驗證器拒絕不合格式者。

理由：排程器以 `strptime` 解析該欄位，格式錯誤會拋出例外並被逐筆的 try/except 吞掉——該筆提醒將永遠不會觸發，且使用者收不到任何錯誤回饋。

#### Scenario: 非法時間格式

- **WHEN** 建立請求帶入 `{"morning": "9am"}`
- **THEN** 系統 SHALL 回傳驗證錯誤，SHALL NOT 寫入資料庫

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

1. **T+0**：推播服藥提醒給用藥者，附【已用藥】確認按鈕
2. **T+20 分鐘**：仍未確認時，推播二次催促給用藥者
3. **T+30 分鐘**：仍未確認時，推播逾時警報給該紀錄的通報對象（`alert_notify_user_id`，即開立者），並將狀態改為 `missed`

各階段的到期判定 SHALL 使用「小於等於」而非精確相等，以容忍 tick 的秒數偏差與延遲。各階段 SHALL 各自維護已送出旗標，確保同一則訊息不重複發送。

#### Scenario: 逾時未確認

- **WHEN** 用藥者於排程時間後 30 分鐘仍未按下【已用藥】
- **THEN** 通報對象 SHALL 收到逾時警報，該紀錄狀態 SHALL 為 `missed`

#### Scenario: 中途完成確認

- **WHEN** 用藥者於 T+10 分鐘按下【已用藥】
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

用藥者按下【已用藥】時，系統 SHALL 將該紀錄狀態更新為 `taken` 並記錄確認時間，且 SHALL 以停用狀態的訊息取代原提醒卡片。狀態為 `pending` 或 `missed` 者皆 SHALL 允許被確認；已為 `taken` 者 SHALL 視為冪等成功。

確認 SHALL 限該紀錄的用藥者本人。

#### Scenario: 逾時後才確認

- **WHEN** 家屬已收到逾時警報（狀態為 `missed`）後，用藥者才按下【已用藥】
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

註銷 SHALL 於規則更新成功之後才執行；更新失敗時規則仍為啟用，SHALL NOT 作廢當日紀錄。未帶 `enabled` 的更新請求（例如只調整提醒時間）SHALL NOT 註銷任何紀錄。

關閉 SHALL NOT 刪除規則本身，亦 SHALL NOT 停用其關聯藥品。同日再次開啟 SHALL NOT 復原已註銷的紀錄——展開以 `(reminder_id, scheduled_at)` 為唯一識別且僅在插入時寫入初始欄位，已存在的紀錄不會被改回 `pending`，該時段當日因此不再推播。

`cancelled` SHALL NOT 為終局狀態：使用者對已註銷的紀錄按下【已用藥】，系統 SHALL 將其狀態改為 `taken`。理由：使用者可能先服了藥才關閉該時段（例如療程結束），最後才按下推播訊息上仍留著的確認；服藥是事實，紀錄應收斂為 `taken`。此與 `missed` 允許事後轉 `taken` 為同一判斷——使用者按下的確認一律優先於系統推得的狀態。此放寬 SHALL NOT 使推播復活：三階查詢限定 `pending`，`taken` 同樣不會被挑中。

用藥歷史 SHALL NOT 列出狀態為 `cancelled` 的紀錄。理由：該狀態是為阻止排程器於同日後續 tick 重新展開而留下的內部記帳，並非使用者的行為；列出會使使用者在歷史中看到一筆自己從未互動、狀態亦無從解讀的紀錄。已由 `cancelled` 轉為 `taken` 者 SHALL 照常列出。

#### Scenario: 關閉後不再催促

- **WHEN** 08:00 的紀錄已展開且尚未確認，使用者於 08:05 關閉該時段規則
- **THEN** 該紀錄狀態 SHALL 為 `cancelled`，用藥者 SHALL NOT 收到 T+20 催促，家屬 SHALL NOT 收到 T+30 逾時警報

#### Scenario: 已確認的紀錄不受關閉影響

- **WHEN** 使用者已按下【已用藥】後才關閉該時段規則
- **THEN** 該紀錄狀態 SHALL 維持 `taken`

#### Scenario: 只調整提醒時間

- **WHEN** 更新請求僅帶 `scheduled_time`，未帶 `enabled`
- **THEN** SHALL NOT 註銷任何當日紀錄

#### Scenario: 關閉後才補按已用藥

- **WHEN** 該時段的紀錄已為 `cancelled`，使用者按下【已用藥】
- **THEN** 該紀錄狀態 SHALL 為 `taken`，且 SHALL 出現在用藥歷史中

#### Scenario: 已註銷的紀錄不進歷史

- **WHEN** 查詢使用者的用藥歷史，其中一筆紀錄狀態為 `cancelled`
- **THEN** 回傳結果 SHALL NOT 包含該筆紀錄

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

### Requirement: 推播的藥品清單得帶出藥丸縮圖

給用藥者的服藥提醒與二次催促，其藥品清單的每一列 SHALL 在該藥品的 `license_number` 已確定且有可用照片時，於藥名旁呈現藥丸縮圖。

沒有照片的藥品 SHALL 維持純文字列，且同一清單中圖文混排 SHALL NOT 使版面破損。`medication_ids` 為空時的版面 SHALL 與本變更前完全相同。

藥品數量仍 SHALL 受既有顯示上限收斂為單行計數。

家屬的逾時警報與錯過時段彙整通知 SHALL NOT 呈現藥丸縮圖——其收件人的問題是「有沒有吃」，不是「該吃哪一顆」，加入藥品外觀只會擴大病情資訊在通知列的暴露面。

#### Scenario: 有照片的藥品

- **WHEN** 某時段的藥品證號已確定且有可用照片
- **THEN** 該列 SHALL 呈現縮圖與藥名

#### Scenario: 同時段圖文混排

- **WHEN** 某時段同時有帶照片與無照片的藥品
- **THEN** 無照片者 SHALL 呈現為純文字列，版面 SHALL NOT 破損

#### Scenario: 家屬卡片不含縮圖

- **WHEN** 逾時警報推播給家屬
- **THEN** 訊息 SHALL NOT 含任何藥丸縮圖

