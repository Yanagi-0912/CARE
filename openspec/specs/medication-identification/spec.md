# medication-identification Specification

## Purpose
TBD - created by archiving change prescription-bag-scan. Update Purpose after archive.
## Requirements
### Requirement: 辨識入口與影像處理邊界

藥袋影像 SHALL 由 LIFF 以 multipart 直接上傳至 `POST /api/medications/prescription-scan`，SHALL NOT 經由 LINE 訊息的媒體處理路徑。

理由：既有媒體路徑（`app/services/line_messaging/handler/media_handler.py` → `app/services/media/mutimedia_processor.py:161`）把所有影像 POST 到 `MEDIA_PARSE_WEBHOOK_URL` 換回一段純文字，藥袋需要的是 schema 約束的結構化輸出；且該路徑以 module-level singleton 被直接 import，無法在禁止 monkey patch 的前提下注入測試替身。

上傳的影像 SHALL NOT 寫入資料庫或持久化儲存，SHALL 僅存在於處理該次請求的記憶體中，於辨識完成或失敗後即釋放。

超過 `PRESCRIPTION_SCAN_MAX_IMAGE_BYTES` 的上傳 SHALL 以 413 拒絕；非影像的 content type SHALL 以 415 拒絕。

#### Scenario: 影像不落地

- **WHEN** 使用者上傳一張藥袋影像並完成辨識
- **THEN** 系統 SHALL NOT 於任何 collection 或檔案系統留存該影像的位元組

#### Scenario: 影像過大

- **WHEN** 上傳的影像大於 `PRESCRIPTION_SCAN_MAX_IMAGE_BYTES`
- **THEN** 系統 SHALL 回傳 413，SHALL NOT 呼叫辨識服務

#### Scenario: 既有媒體路徑不受影響

- **WHEN** 使用者於 LINE 聊天室傳送任意影像
- **THEN** 該事件 SHALL 仍由既有的媒體處理路徑處理，SHALL NOT 進入藥袋辨識流程

### Requirement: 結構化辨識輸出

辨識服務 SHALL 以 schema 約束的方式輸出，SHALL NOT 回傳自由格式文字再行解析。

輸出 SHALL 包含調劑機構、病患姓名、調劑日期，以及一組藥品項目；每個藥品項目 SHALL 包含藥品名稱、單位含量、總數量、用法原文、頻次代碼、每次劑量、服用時機、療程天數與適應症。

`用法原文` SHALL 保留藥袋上的原始字串，SHALL NOT 以正規化後的結果覆寫——使用者核對時需要能對照藥袋上實際印的內容。

頻次代碼 SHALL 收斂為 `QD`、`BID`、`TID`、`QID`、`HS`、`PRN`、`OTHER` 其中之一；無法歸類者 SHALL 為 `OTHER`，SHALL NOT 臆測。

任何欄位無法從影像判讀時 SHALL 為空值，SHALL NOT 以推測值填補。

#### Scenario: 頻次無法歸類

- **WHEN** 藥袋上的用法為「每週一、三、五各一顆」
- **THEN** 頻次代碼 SHALL 為 `OTHER`，`用法原文` SHALL 保留該字串

#### Scenario: 欄位缺漏

- **WHEN** 藥袋上未印療程天數
- **THEN** 該欄位 SHALL 為空值，SHALL NOT 以總數量除以每日次數推算

### Requirement: 藥證庫校驗

每個辨識出的藥品名稱 SHALL 與藥證庫（由藥品許可證資料集與藥品外觀資料集離線建置）做模糊比對。

比對命中時，系統 SHALL 以藥證庫的中文品名、英文品名與許可證字號補齊該筆藥品，並將該筆的名稱信心度提升為高。

比對未命中時，該筆藥品的名稱信心度 SHALL 為低，SHALL NOT 因模型自述的信心度而視為高。

理由：視覺模型把「脈優錠」讀成形近的其他藥名時，模型本身的信心度仍然很高，只有外部字典能發現該字串不對應任何一張核准藥證。藥證庫是本能力唯一能偵測此類錯誤的機制。

藥證庫檔案不存在或無法載入時，服務 SHALL 於啟動時記錄錯誤，且所有辨識結果的名稱信心度 SHALL 一律為低，SHALL NOT 讓應用啟動失敗。

#### Scenario: 藥名比對不到任何藥證

- **WHEN** 辨識出的藥品名稱在藥證庫中沒有相似度足夠的項目
- **THEN** 該筆的名稱信心度 SHALL 為低

#### Scenario: 藥證庫缺席

- **WHEN** `DRUG_CATALOG_PATH` 指向的檔案不存在
- **THEN** 應用 SHALL 正常啟動，所有辨識結果的名稱信心度 SHALL 為低

### Requirement: 信心度分級決定確認方式

系統 SHALL 依辨識結果的信心度給出分級，並以分級決定使用者可用的操作：

- **高**：所有藥品的名稱皆通過藥證庫校驗，且用藥對象、頻次代碼皆非空。使用者 MAY 一鍵確認整份草稿。
- **中**：任一藥品的名稱未通過校驗，或任一必要欄位為空。使用者 SHALL NOT 取得一鍵確認，SHALL 逐筆檢視並補齊後才能提交。
- **低**：判定影像不是藥袋，或未辨識出任何藥品。系統 SHALL NOT 建立草稿，SHALL 回覆重拍指引與手動建立的替代路徑。

#### Scenario: 有藥名比對不到

- **WHEN** 三種藥中有一種未通過藥證庫校驗
- **THEN** 分級 SHALL 為中，介面 SHALL NOT 提供一鍵確認

#### Scenario: 不是藥袋

- **WHEN** 上傳的影像未辨識出任何藥品項目
- **THEN** 系統 SHALL NOT 建立草稿，SHALL 回傳可辨識的失敗原因與重拍指引

### Requirement: 確認閘門

辨識結果 SHALL 先寫入草稿，SHALL NOT 直接建立 `medications` 或 `medication_reminders`。

只有在使用者提交該草稿時，系統才 SHALL 建立藥品與提醒規則。

呈現草稿的介面 SHALL 標示該結果由自動辨識產生、請對照藥袋確認。

#### Scenario: 只辨識未提交

- **WHEN** 使用者上傳藥袋、取得草稿後直接關閉頁面
- **THEN** 系統 SHALL NOT 建立任何藥品或提醒規則

#### Scenario: 提交後才建立

- **WHEN** 使用者提交草稿
- **THEN** 系統 SHALL 依草稿內容建立藥品，並將藥品關聯至對應時段的提醒規則

### Requirement: 用藥對象的判定與確認

系統 SHALL 以辨識出的病患姓名比對操作者家庭族譜內成員的姓名，作為用藥對象的預設建議。

比對結果 SHALL 僅作為預設值，使用者 SHALL 在提交前確認或改選對象。系統 SHALL NOT 在未經確認的情況下依姓名比對結果建立提醒。

提交時指定的用藥對象不在操作者族譜內時，系統 SHALL 拒絕，SHALL NOT 建立任何藥品或規則。

姓名比對不到任何成員時，對象欄位 SHALL 為空並要求使用者選擇。

#### Scenario: 姓名命中族譜成員

- **WHEN** 藥袋上的病患姓名與族譜內一位成員相符
- **THEN** 該成員 SHALL 為預設對象，介面 SHALL 仍要求使用者確認

#### Scenario: 對象不在族譜內

- **WHEN** 提交時指定的 `user_id` 不在操作者族譜中
- **THEN** 系統 SHALL 拒絕並回傳錯誤，SHALL NOT 建立任何藥品或規則

### Requirement: 頻次代碼映射至時段

提交草稿時，系統 SHALL 依頻次代碼決定該藥品要關聯到哪些時段：

- `QD` → `morning`；但辨識出的服用時機（`timing`）為 `bedtime` 時 SHALL 改為 `bedtime`
- `BID` → `morning`、`evening`
- `TID` → `morning`、`noon`、`evening`
- `QID` → `morning`、`noon`、`evening`、`bedtime`
- `HS` → `bedtime`
- `OTHER` → 不自動映射；使用者尚未決定時段時 SHALL 要求使用者選擇後才能提交；使用者明確選擇「這個藥不用定時提醒我」時 SHALL 允許以不關聯任何時段的方式提交

`timing` 對時段映射的影響 SHALL 僅限於前述 `QD` 的例外：`timing` 為 `before_meal`、`after_meal` 或 `empty_stomach` 時 SHALL NOT 影響時段映射；`BID`、`TID`、`QID` 等一日多次的頻次，即使 `timing` 為 `bedtime`，SHALL 仍依原有頻次映射，SHALL NOT 因 `timing` 而改變。

使用者 SHALL 能於提交前覆寫任一藥品的時段對應；使用者的覆寫 SHALL 優先於 `timing` 對映射的任何影響。

理由：「尚未指定時段」與「已經決定不要提醒」是兩種不同的使用者狀態，不能用同一種拒絕方式處理。前者代表使用者還沒看過這個欄位、系統不該替他猜一個服藥時間；後者代表使用者已經看過、明確表示這顆藥不需要定時提醒，此時仍強迫他勾一個時段，只會逼出一個不代表真實情況的選擇。這與 `PRN` 藥品「不建立定時提醒」是同一種最終結果，差別只在於誰做出這個判斷——`PRN` 是系統依安全規則自動判定，`OTHER` 這裡是使用者的主動選擇，兩者都 SHALL 建立藥品資料、SHALL NOT 將其關聯至任何時段的提醒規則。

`QD` 的 `timing` 例外理由：`timing` 是辨識階段就已經抽出的欄位，`bedtime` 是其中唯一明確指向單一時段的值——「睡前服用」不是與進食的關係，而是直接陳述時段本身。一日僅一次的藥品若標示睡前，把預設提醒排在早上，會讓使用者依錯誤的預設時段服藥，且必須每次都手動更正才能得到正確結果；系統不該在已經取得能判斷這件事的資訊時，仍然給出一個明知有更精確答案的預設值。`before_meal`／`after_meal`／`empty_stomach` 不受此例外涵蓋，因為它們描述的是與進食的相對關係，不指向任何一個固定時段，無法據以推得該對應哪一次服藥。一日多次的頻次（`BID`／`TID`／`QID`）同樣不受影響：多劑量藥袋上出現「睡前」多半只限定其中最後一次劑量，頻次代碼本身已經是「一天吃幾次」這件事上更明確、更不容易被誤讀的陳述，用單一 `timing` 值覆寫整組時段映射屬於過度推論，因此刻意不做。

#### Scenario: TID 映射三個時段

- **WHEN** 某藥品的頻次代碼為 `TID` 且使用者未覆寫
- **THEN** 該藥品 SHALL 關聯至該用藥者的 `morning`、`noon`、`evening` 三筆規則

#### Scenario: QD 標示睡前時映射到 bedtime

- **WHEN** 某藥品的頻次代碼為 `QD`、辨識出的 `timing` 為 `bedtime`，且使用者未覆寫時段
- **THEN** 該藥品 SHALL 關聯至該用藥者的 `bedtime` 規則，SHALL NOT 關聯至 `morning`

#### Scenario: QD 沒有 timing 時維持既有預設

- **WHEN** 某藥品的頻次代碼為 `QD`、未辨識出 `timing`，且使用者未覆寫時段
- **THEN** 該藥品 SHALL 關聯至該用藥者的 `morning` 規則

#### Scenario: timing 為飯前後或空腹時不影響映射

- **WHEN** 某藥品的頻次代碼為 `QD`、辨識出的 `timing` 為 `before_meal`、`after_meal` 或 `empty_stomach`
- **THEN** 該藥品 SHALL 仍關聯至該用藥者的 `morning` 規則，時段映射 SHALL NOT 因 `timing` 而改變

#### Scenario: 一日多次頻次不因睡前 timing 改變映射

- **WHEN** 某藥品的頻次代碼為 `TID`，辨識出的 `timing` 為 `bedtime`
- **THEN** 該藥品 SHALL 仍關聯至該用藥者的 `morning`、`noon`、`evening` 三筆規則，SHALL NOT 因 `timing` 而改變

#### Scenario: 使用者覆寫優先於 timing

- **WHEN** 某藥品的頻次代碼為 `QD`、辨識出的 `timing` 為 `bedtime`，且使用者明確指定時段為 `morning`
- **THEN** 該藥品 SHALL 關聯至該用藥者的 `morning` 規則

#### Scenario: 無法歸類的頻次、使用者尚未決定

- **WHEN** 某藥品的頻次代碼為 `OTHER`，使用者尚未指定時段
- **THEN** 系統 SHALL NOT 自動指定時段，SHALL 要求使用者選擇後才能提交

#### Scenario: 無法歸類的頻次、使用者選擇不提醒

- **WHEN** 某藥品的頻次代碼為 `OTHER`，使用者明確選擇「這個藥不用定時提醒我」而不指定任何時段
- **THEN** 系統 SHALL 允許提交並建立該藥品，該藥品的 id SHALL NOT 出現在任何提醒規則的 `medication_ids` 中

### Requirement: 需要時服用的藥品不建立定時提醒

頻次代碼為 `PRN` 的藥品，系統 SHALL 建立藥品資料，SHALL NOT 將其關聯至任何時段的提醒規則。

介面 SHALL 明確告知該藥品不會定時提醒，以及其原因。

理由：把「不舒服時才吃」的備用藥建成定時提醒，會使長輩依提醒定時服用備用藥。這是用藥安全問題，不是呈現偏好。

#### Scenario: PRN 藥品

- **WHEN** 草稿中某藥品的頻次代碼為 `PRN` 且使用者提交
- **THEN** 系統 SHALL 建立該藥品，該藥品的 id SHALL NOT 出現在任何提醒規則的 `medication_ids` 中

#### Scenario: PRN 的告知

- **WHEN** 草稿中含有 `PRN` 藥品
- **THEN** 介面 SHALL 顯示該藥品不會定時提醒的說明

### Requirement: 重新啟用既有規則的揭露與收斂

提交草稿時，若某藥品要關聯到的時段命中一筆使用者在該時段目前不可排程（`enabled` 為否、`end_date` 已過期，或 `start_date` 尚未到）的既有提醒規則，系統 SHALL 重新啟用該規則使其恢復可排程，SHALL NOT 為同一位使用者、同一時段另外建立第二筆規則。

提交結果 SHALL 回報本次實際重新啟用的時段清單。

呈現草稿的介面 SHALL 在使用者確認提交前，針對每一個命中「目前不可排程」規則的時段，告知使用者：該時段目前是關閉的，且若該時段已掛有其他藥品，這些藥品也會一併恢復收到提醒。

理由：一個使用者、一個時段永遠只有一份提醒規則，重新啟用時無法只復活「這次提交的藥」而不動到同一時段既有的其他藥——命中一筆原本停用或已過期的規則，代表使用者當初主動關掉它、或它的療程已經結束，這次提交若在使用者不知情的情況下把它悄悄復活，使用者會在事後的提醒列表發現多了一則自己沒印象重新開啟的推播。因此揭露必須發生在確認送出「之前」，讓使用者能在看到後果的情況下決定要不要送出，而不是送出後才被告知既成事實。

#### Scenario: 命中已關閉的時段只重新啟用一筆規則

- **WHEN** 使用者提交的藥品要關聯到的時段命中一筆目前停用、已過期或尚未到 `start_date` 的既有規則
- **THEN** 系統 SHALL 重新啟用該規則，SHALL NOT 為同一位使用者、同一時段建立第二筆規則

#### Scenario: 提交結果回報重新啟用的時段

- **WHEN** 本次提交把某個時段的規則從不可排程改回可排程
- **THEN** 提交結果 SHALL 在重新啟用的時段清單中包含該時段

#### Scenario: 送出前的揭露

- **WHEN** 使用者尚未確認提交，且草稿中的藥品會命中一筆目前關閉、且已掛有其他藥品的既有規則
- **THEN** 介面 SHALL 在確認送出前告知該時段目前是關閉的，且該時段既有的其他藥品會一併恢復收到提醒

### Requirement: 草稿的生命週期

草稿 SHALL 存於獨立的 `prescription_drafts` collection，並以 `PRESCRIPTION_DRAFT_TTL_MINUTES` 為存活時間，由資料庫的 TTL 索引自動清除。

草稿 SHALL 僅能由建立它的使用者讀取與提交；其他使用者的請求 SHALL 以 404 拒絕，SHALL NOT 揭露該草稿是否存在。

草稿 SHALL 只能成功提交一次。已提交的草稿再次提交 SHALL 回傳既有的建立結果，SHALL NOT 重複建立藥品或規則。

已過期的草稿被提交時 SHALL 以 410 拒絕，並提示重新掃描。

#### Scenario: 重複提交

- **WHEN** 同一份草稿被提交兩次
- **THEN** 第二次 SHALL 回傳第一次的建立結果，資料庫中 SHALL NOT 出現重複的藥品

#### Scenario: 他人的草稿

- **WHEN** 使用者 B 以使用者 A 的 `draft_id` 發出請求
- **THEN** 系統 SHALL 回傳 404

### Requirement: 辨識失敗的退路

辨識服務逾時、回傳無效結構或外部呼叫失敗時，系統 SHALL 回傳可區分的失敗原因，SHALL NOT 以單一泛用錯誤訊息涵蓋所有情況。

失敗原因 SHALL 至少區分「影像判讀失敗（建議重拍）」「不是藥袋」與「服務暫時無法使用（建議稍後再試）」，因為三者對使用者的下一步指示完全不同。

任何失敗情境下，介面 SHALL 提供手動建立提醒的替代路徑。

辨識結果中出現多於一個病患姓名或多份調劑日期時，系統 SHALL 提示影像中可能包含多個藥袋並建議一次拍一個，同時 SHALL 仍回傳已辨識到的項目。

#### Scenario: 辨識服務逾時

- **WHEN** 呼叫辨識服務逾時
- **THEN** 系統 SHALL 回傳「服務暫時無法使用」的失敗原因，SHALL NOT 回傳「請重拍」

#### Scenario: 單張影像含多個藥袋

- **WHEN** 辨識結果出現兩個不同的病患姓名
- **THEN** 系統 SHALL 於回應中標記可能含多個藥袋，SHALL 仍回傳已辨識到的藥品項目

### Requirement: 功能開關與隱私

本能力 SHALL 由 `PRESCRIPTION_SCAN_ENABLED` 控制，預設為關閉。關閉時相關端點 SHALL 回傳 404，LIFF SHALL NOT 顯示掃描入口。

藥品的適應症 SHALL 僅對用藥者本人與其族譜成員於 LIFF 中可見，SHALL NOT 出現在任何推播訊息中。

理由：適應症會直接揭露病情。推播訊息會出現在通知列與鎖定畫面，可能被非預期的人看到。

#### Scenario: 功能關閉

- **WHEN** `PRESCRIPTION_SCAN_ENABLED` 為 `false` 且使用者呼叫掃描端點
- **THEN** 系統 SHALL 回傳 404

#### Scenario: 適應症不進推播

- **WHEN** 某藥品帶有適應症且該時段觸發推播
- **THEN** 推播內容 SHALL 僅含藥品名稱，SHALL NOT 含適應症

### Requirement: 草稿攜帶藥證候選清單

辨識草稿中的每一筆藥品，SHALL 在藥名命中多張藥證時附上候選清單（證號、中文品名、外觀欄位），供核對畫面呈現給使用者挑選。

候選清單 SHALL NOT 參與信心度分級的判定。分級規則維持既有定義：藥名是否通過藥證庫校驗，與命中幾張藥證無關。

理由：候選是新增的資訊，不是新的判定依據。把「候選有幾張」納入分級，會讓大量藥名正確、只是同名藥證多的藥品被誤降級，稀釋低信心標記的意義。

#### Scenario: 多候選不影響分級

- **WHEN** 草稿中所有藥名皆通過校驗，其中一筆命中多張藥證
- **THEN** 該草稿的信心度分級 SHALL 與該筆只命中一張時相同

### Requirement: 提交時接受使用者挑定的藥證

提交草稿時，每筆藥品 SHALL 能帶入使用者挑定的 `license_number`。系統 SHALL 以該值建立藥品。

使用者挑定的證號 SHALL 限於該筆藥品的候選清單之內。不在候選內的證號 SHALL 被丟棄，該筆藥品 SHALL 以空證號建立；系統 SHALL NOT 因此拒絕整份提交，且 SHALL 於回應中列出哪些藥品的證號被丟棄。

理由：候選清單受藥名約束，是這條路徑上唯一的 ground truth——接受清單外的任意證號，等於讓照片可以指向任何藥品，「證號不確定就不顯示照片」那道邊界也就失效了。但**丟棄該證號已經完全保住這道邊界**（不會顯示錯誤照片），拒絕整份提交則額外付出不成比例的代價：候選外的證號實務上只來自兩種情形——用戶端瑕疵，或使用者在核對畫面改了藥名而證號未隨之失效。後者的正確語意本來就是「證號不再適用」（見「藥名被編輯時證號與照片一併失效」）。而拒絕整份提交會讓使用者連同已核對過的其他藥品一起失去，錯誤訊息還會要他「重新選擇」——但改名後根本沒有候選可選，唯一的出路是重掃。這與本能力「照片是附加價值，不是提交的必要條件」自相矛盾。

丟棄 SHALL NOT 是靜默的：使用者明確選過的東西被系統丟掉，必須讓他知道，否則他會以為照片會出現而事後困惑。

#### Scenario: 挑定候選內的證號

- **WHEN** 提交時帶入候選清單中的某個證號
- **THEN** 建立的藥品 SHALL 帶有該證號

#### Scenario: 帶入候選外的證號

- **WHEN** 提交時帶入不在該筆候選清單中的證號
- **THEN** 該筆藥品 SHALL 以空證號建立，其餘藥品 SHALL 正常建立，回應 SHALL 列出該筆被丟棄

### Requirement: 藥名被編輯時證號與照片一併失效

使用者在核對畫面修改某筆藥品的名稱時，該筆的 `license_number` SHALL 被清除，其藥丸照片與外觀描述 SHALL 隨之不再呈現。

理由：照片依附於證號，證號依附於藥名。保留改名前的證號會讓畫面顯示的是另一種藥的照片——這正是本能力要避免的錯誤。

#### Scenario: 修改藥名

- **WHEN** 使用者把某筆藥品的名稱改成別的字串
- **THEN** 該筆的 `license_number` SHALL 為空，SHALL NOT 顯示原本的藥丸照片

