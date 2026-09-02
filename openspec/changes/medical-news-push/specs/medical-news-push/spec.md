# medical-news-push Specification

## Purpose

定義每日醫療消息卡的兩層選材、內容來源的信任邊界、判定失敗時的處置、輸出的內容限制，以及「認同分享」的收件人判定與零洩漏保證。實作位於 `app/services/medical_news/`、`app/repositories/medical_news_repository.py`、`app/models/medical_news.py` 與 `app/services/line_messaging/flex/medical_news_flex.py`。

## Requirements

### Requirement: 每日一則與兩層選材

系統 SHALL 每日為每位使用者至多推播一則消息卡。命中該使用者當日仍有效之用藥的警訊為 Tier 1，未命中時 SHALL 退回一般衛教時事為 Tier 2。

收件人 SHALL 為全體使用者，SHALL NOT 僅限有用藥資料者——Tier 2 存在的理由正是沒有用藥資料的人。

兩層 SHALL 使用可辨別的不同版面（標題文案與底色皆不同），SHALL NOT 以同一張版面僅替換內容。

理由：每日必推的代價是使用者會學會忽略這張卡。兩層若無法分辨，Tier 1 的高價值警訊會被 Tier 2 一起稀釋，而稀釋的過程沒有任何訊號——不報錯、不留 log，只表現為「使用者不再點卡片」。

某日若有多則 Tier 1 命中，SHALL 依 `recall` > `safety` > `supply` > `education` 取優先序最高者，同優先序內取發布日最新者；其餘 SHALL 留待後續日期。連發多則「你的藥有問題」對高齡使用者是恐慌而非資訊。

#### Scenario: 命中用藥時推 Tier 1

- **WHEN** 使用者當日有效的用藥在索引中存在未推播過的消息
- **THEN** 系統 SHALL 推播 Tier 1 卡片，且當日 SHALL NOT 再推播任何消息卡

#### Scenario: 未命中時退回 Tier 2

- **WHEN** 使用者的用藥在索引中沒有可推播的消息
- **THEN** 系統 SHALL 推播 Tier 2 卡片

#### Scenario: 沒有用藥資料的使用者

- **WHEN** 使用者沒有任何有效用藥
- **THEN** 系統 SHALL 推播 Tier 2 卡片，SHALL NOT 跳過該使用者

#### Scenario: 兩層皆無內容

- **WHEN** Tier 1 與 Tier 2 皆無可推播的內容
- **THEN** 系統 SHALL NOT 推播，SHALL NOT 送出沒有內容的卡片

#### Scenario: 多則命中取優先序最高

- **WHEN** 同一位使用者同日有 `recall` 與 `education` 兩則命中
- **THEN** 系統 SHALL 推播 `recall` 那則

### Requirement: 內容來源限定官方域

Tier 1 的候選 SHALL 取自既有的白名單搜尋路徑（`RAG_WEB_SEARCH_SITE_FILTER` 與 `whitelist.is_allowed_url`），SHALL NOT 新增搜尋路徑，SHALL NOT 放寬白名單。

Tier 2 的候選 SHALL 取自既有知識庫 collection，SHALL NOT 新增外部依賴。

任何缺少可點網址的來源 SHALL NOT 產生消息卡。

理由：消息卡是主動推播，使用者沒有在發問，任何內容都會被當成系統的背書。而健康類關鍵字的開放搜尋結果商業污染最重——白名單的收錄判準第三條就是「無商業銷售動機」。網址是收件人唯一能自行查證的東西，分享出去的卡片尤其。

#### Scenario: 非白名單網域

- **WHEN** 搜尋結果的網址不在白名單內
- **THEN** 系統 SHALL 丟棄該筆，SHALL NOT 對其發出抓取請求

#### Scenario: 來源無網址

- **WHEN** 某來源的文章結構上不提供網址
- **THEN** 該來源的內容 SHALL NOT 成為消息卡的候選

### Requirement: 字面比對先於模型判定

系統 SHALL 在呼叫任何模型之前，先以正規化後的字面比對確認消息文字中出現該藥名或成分；未通過者 SHALL 丟棄，SHALL NOT 進入模型判定。

此比對 SHALL NOT 使用模糊比對。

此比對的職責是**成本篩選**，不是精確判定；「這則消息是否確實在講這個藥」由結構化判定的 `is_about_this_drug` 回答。已知限制：中文無詞界，較短的藥名會命中包含它的較長藥名（如「胃能錠」命中「欲胃能錠」），此限制 SHALL 以測試明文記錄。

#### Scenario: 字面未命中不進入模型

- **WHEN** 搜尋結果的標題與摘要皆未出現該藥名或成分
- **THEN** 系統 SHALL 丟棄該筆，且 SHALL NOT 呼叫判定模型

### Requirement: 判定失敗時 fail closed

當結構化判定呼叫失敗（逾時、例外、輸出不合法）時，系統 SHALL 丟棄該筆候選，SHALL NOT 據以推播，SHALL NOT 以預設值代替判定結果。

單一藥品或單一候選的失敗 SHALL NOT 中斷該輪索引的其餘部分。

**此行為與 `rag-crag` 明文規定的「grader 失敗時降級為照常生成」刻意相反**，理由是兩者的等待方不同：CRAG 那條路上使用者正在等答案，沒答案比不完美的答案糟；這條路上沒有人在等，沒推遠比推錯好。維護時 SHALL NOT 以「兩處行為不一致」為由將本條改為降級。

#### Scenario: 判定逾時

- **WHEN** 某個藥品的判定呼叫逾時
- **THEN** 系統 SHALL 跳過該藥品，SHALL NOT 推播，且其餘藥品的索引 SHALL 照常進行

#### Scenario: 輸出不合法

- **WHEN** 判定回傳的 `concern_kind` 不在允許值域內
- **THEN** 系統 SHALL 視為判定未發生並丟棄該筆，SHALL NOT 以任一預設值續行

### Requirement: 時效門檻

進入 Tier 1 的消息 SHALL 具備可解析的發布日期，且該日期 SHALL 在設定的天數範圍內。

發布日期無法解析、缺席、或落在未來超過一日者 SHALL NOT 進入 Tier 1。

理由：政府網頁的日期位置不一致，抽取本來就會失敗；缺資料時的預設必須是排除，否則「不知道多舊」的消息會混進「近期警訊」。

#### Scenario: 抽不到日期

- **WHEN** 某則消息的發布日期無法從內容抽出
- **THEN** 該則 SHALL NOT 進入 Tier 1

### Requirement: 輸出內容限制

消息卡的文案 SHALL NOT 包含停藥、換藥、改吃、自行調整劑量、增量或減量的建議。

系統 SHALL 在模型提示與送出前的字串檢查兩處各實施一次此限制。字串檢查命中時 SHALL 整則丟棄，SHALL NOT 嘗試改寫後再送。

Tier 1 卡片 SHALL 包含固定的行動呼籲「請與您的醫師或藥師確認」，該文案 SHALL 為常數，SHALL NOT 由模型產生。

理由：提示可被繞過，字串比對不會；而改寫等於讓模型再賭一次，這道防線存在的前提正是不能相信模型會自己守住。

#### Scenario: 摘要含停藥建議

- **WHEN** 判定產出的摘要含「建議停藥」
- **THEN** 系統 SHALL 丟棄整則，SHALL NOT 寫入索引，SHALL NOT 改寫後保留

### Requirement: 適應症不得進入推播

`Medication.indication`、`Medication.spc_indication`、`Medication.spc_indication_summary` SHALL NOT 出現在任何消息卡或分享卡中。

卡片的組建介面 SHALL NOT 接受這些欄位作為參數——以介面排除而非以內容過濾，呼叫端才不存在誤傳的路徑。

理由：適應症直接揭露病情，其揭露範圍遠大於「這個藥有新消息」。此禁令沿用 `app/models/medication.py` 既有的條文。

#### Scenario: 卡片組建介面

- **WHEN** 檢視任一消息卡組建函式的簽章
- **THEN** 其參數 SHALL NOT 含任何適應症欄位

### Requirement: 摘要為中性第三人稱

索引寫入的摘要 SHALL 以中性第三人稱撰寫，SHALL NOT 使用第二人稱或個人化脈絡。

個人化的呈現（藥名、與使用者的關聯）SHALL 只存在於 Tier 1 卡片的標題列與藥品列。

理由：這使分享路徑的零洩漏成為版面問題而非文字改寫問題——分享時只要不帶那兩行即可，不需要在分享路徑上再呼叫一次模型。

#### Scenario: 摘要不含第二人稱

- **WHEN** 索引產出一則摘要
- **THEN** 該摘要 SHALL 可原樣用於分享卡，SHALL NOT 需要改寫

### Requirement: 分享的收件人判定

按下分享時，系統 SHALL 以分享者的族譜成員（扣除本人）為收件人。

系統 SHALL NOT 使用 `FamilyAuthorizationService.notification_recipients()` 或 `NOTIFICATION_POLICY` 判定分享的收件人。

理由：`NOTIFICATION_POLICY` 回答的是「這位當事人出事時該通知誰」，與「我主動分享一則公開消息給誰」是不同的信任。共用會讓兩種語意互相污染——日後調整通報政策會在毫無關聯的地方改變分享行為。

族譜為空時 SHALL 回覆提示並引導至邀請流程，SHALL NOT 靜默失敗。

系統 SHALL 對每位分享者施加每日分享次數上限。

#### Scenario: 不查通報政策

- **WHEN** 使用者按下分享
- **THEN** 系統 SHALL NOT 呼叫 `notification_recipients()`

#### Scenario: 族譜為空

- **WHEN** 分享者的族譜沒有任何成員
- **THEN** 系統 SHALL 回覆提示訊息，SHALL NOT 靜默結束

### Requirement: 分享卡零洩漏

分享卡 SHALL 只包含標題、中性摘要、來源名稱、來源網址與分享者名稱。

分享卡 SHALL NOT 包含藥名、Tier 標示，或任何得以推知分享者用藥狀態的內容。

分享卡 SHALL NOT 帶有分享按鈕——否則一則消息會在族譜中無限轉傳。

#### Scenario: 由 Tier 1 卡片分享

- **WHEN** 使用者分享一張帶有藥名的 Tier 1 卡片
- **THEN** 收件人收到的卡片 SHALL NOT 含該藥名

### Requirement: 去重與推播權搶佔

系統 SHALL 以 `(user_id, news_ref)` 唯一索引同時實現「同一則不重複推播給同一位使用者」與「多實例並存時的推播權搶佔」；SHALL 於插入成功後才推播，SHALL NOT 先查詢再寫入。

系統 SHALL 以 `(recipient_id, news_ref)` 唯一索引確保同一則消息對同一位收件人只送達一次，不論有幾位家人按下分享。

推播失敗 SHALL NOT 重試、SHALL NOT 補推。延遲後的消息卡已失去時效意義。

#### Scenario: 另一實例已搶到

- **WHEN** 插入因唯一索引衝突而失敗
- **THEN** 本實例 SHALL 跳過該次推播，SHALL NOT 拋出例外中斷該輪

#### Scenario: 多位家人分享同一則

- **WHEN** 兩位家人先後分享同一則消息給同一位收件人
- **THEN** 該收件人 SHALL 只收到一次

### Requirement: 卡片內容隨推播落地

推播紀錄 SHALL 一併保存該次卡片的標題、摘要、來源名稱與來源網址。

分享時 SHALL 由該紀錄重建卡片，SHALL NOT 回頭查詢原始來源。

理由：`news_ref` 是雜湊，反解不回來源網址，而分享的 postback 只帶得動它；且分享卡應顯示分享者當時看到的內容——知識庫文章可能已因重新切片而消失，官方公告也可能已被修訂。

#### Scenario: 來源已消失仍可分享

- **WHEN** 使用者分享一則其來源文章已從知識庫移除的消息
- **THEN** 系統 SHALL 由推播紀錄重建卡片並正常送出

### Requirement: 索引與推播分離

索引與推播 SHALL 為兩支獨立的排程，SHALL 各自登記心跳。

索引整輪失敗 SHALL NOT 影響推播；推播 SHALL 使用既有索引照常執行。

理由：兩者的成本模型不同（索引為 O(不重複藥數)、推播為 O(使用者數)），失敗模式也不同——政府站台逾時是常態，合併會讓前者拖垮後者。心跳合併登記則會讓其中一支停擺被另一支掩蓋：外觀健康，內容卻永遠停在停擺那一天。

#### Scenario: 索引失敗當日仍有推播

- **WHEN** 當日索引因上游站台不可用而整輪失敗
- **THEN** 推播 SHALL 照常執行並使用既有索引內容
