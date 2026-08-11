## MODIFIED Requirements

### Requirement: 建立與查詢知識回報

系統 SHALL 將知識回報持久化於 MongoDB，並允許已登入使用者建立回報與查詢自己的回報列表。每筆回報 SHALL 含唯一 `report_id`、`line_user_id`、`status`（pending／reviewing／resolved／rejected）、`reason`、`question`，以及可選的補充說明與來源 URL 列表。

透過使用者端建立端點（`POST /api/knowledge-reports`）送出的手動回報，SHALL 額外滿足下列約束：

1. `user_source_urls` SHALL 為必填，至少 1 個、至多可設定的上限（預設 3）；每個 URL 的長度 SHALL 有上限
2. `user_note` SHALL 為必填；`question`、`user_note` 皆 SHALL 有長度上限，且僅含空白的字串 SHALL 視為未填而拒絕
3. 每個 `user_source_urls` 元素 SHALL 於 **建立當下** 通過允許網域白名單，SHALL NOT 延後到核准階段才檢驗
4. `reason` SHALL 由呼叫端提供，且 SHALL 維持為純標籤——系統 SHALL NOT 依 `reason` 的值改變建立、審核或 ingest 的行為

未滿足欄位形狀約束（必填、數量、長度）時 SHALL 以 422 拒絕。URL 未通過白名單時 SHALL 以 400 拒絕，且回應 SHALL 以機器可判讀的錯誤碼區分「網址本身不合法」與「網址不在白名單」，並 SHALL 一次列出全部不合格的 URL，SHALL NOT 只回報第一個。任何一項驗證失敗時 SHALL NOT 建立回報。

本需求的收緊 SHALL 只作用於使用者端建立端點。`KnowledgeReportService.create` 本身 SHALL NOT 強制 URL 必填或執行白名單驗證——自動建報路徑（web fallback）內部即呼叫該方法，且其失敗會被上層的例外處理吞掉，若在該層強制驗證，白名單一經收緊即會造成自動建報靜默停止。

#### Scenario: 使用者建立回報

- **WHEN** 已驗證使用者提交 question 與合法 reason
- **THEN** 系統建立 status=pending 的回報並回傳 report_id

#### Scenario: 使用者列出自己的回報

- **WHEN** 已驗證使用者請求列表
- **THEN** 僅回傳該使用者的回報，依建立時間新到舊

#### Scenario: 手動回報缺少來源 URL

- **WHEN** 使用者以建立端點送出未含任何 `user_source_urls` 的請求
- **THEN** 回傳 422，且不建立回報

#### Scenario: 手動回報缺少說明

- **WHEN** 使用者送出 `user_note` 為空或僅含空白字元的請求
- **THEN** 回傳 422，且不建立回報

#### Scenario: 建立當下即擋下非白名單 URL

- **WHEN** 使用者送出含非允許網域 URL 的建立請求
- **THEN** 回傳 400 並標示錯誤碼為「不在白名單」，不建立回報，該 URL SHALL NOT 進入待審佇列

#### Scenario: 一次回報全部不合格的 URL

- **WHEN** 使用者送出兩個皆不合格的 URL
- **THEN** 400 回應同時列出兩個 URL，而非只列出第一個

#### Scenario: 不合法網址與非白名單網址可區分

- **WHEN** 使用者送出含反斜線等不該出現字元的網址
- **THEN** 回傳 400 並標示錯誤碼為「網址不合法」，與「不在白名單」為不同錯誤碼

#### Scenario: service 層不因白名單而拒絕建立

- **WHEN** 直接以非白名單 URL 呼叫 `KnowledgeReportService.create`
- **THEN** 回報仍建立成功，白名單驗證 SHALL NOT 發生於此層

### Requirement: Agent 可提交回報

系統 SHALL 提供 Agent tool `submit_knowledge_report`，在已知 line_user_id 的對話脈絡下建立 pending 回報。

該工具的來源 URL 參數 SHALL 維持選填。系統 SHALL NOT 要求代理必須提供來源 URL 才能完成工具呼叫——強制必填等同要求語言模型在使用者未提供連結時自行生成一個，而生成的允許網域連結會通過白名單、進入待審佇列並可能被核准後抓取。

工具收到的 URL 若未通過白名單，系統 SHALL 丟棄該 URL 並記錄，SHALL NOT 因此讓工具呼叫失敗——失敗會促使代理改寫參數重試，而改寫的方向正是生成更像允許網域的網址。未提供 URL 時 SHALL 維持「未提供」的語意，SHALL NOT 轉換為空列表。

#### Scenario: Tool 建立 pending

- **WHEN** Agent 呼叫 submit_knowledge_report 且脈絡有 line_user_id
- **THEN** 建立 pending 回報並回傳 report_id 摘要

#### Scenario: Tool 未附 URL 仍可建報

- **WHEN** Agent 呼叫 submit_knowledge_report 且未提供來源 URL
- **THEN** 回報仍建立成功，來源 URL 維持「未提供」而非空列表

#### Scenario: Tool 提供的非白名單 URL 被丟棄

- **WHEN** Agent 呼叫 submit_knowledge_report 並提供一個非允許網域的 URL
- **THEN** 回報仍建立成功且工具回傳成功訊息，該 URL 不出現在回報的來源列表中

### Requirement: 同 URL 待審回報去重

建立 **web fallback 自動回報** 前，若既有 `pending` 或 `reviewing` 回報的 `user_source_urls` 含任一即將寫入的 URL，系統 SHALL 刪除該舊回報，再建立新回報。

此刪除 SHALL 只適用於自動建報路徑。使用者手動建立的回報 SHALL NOT 觸發任何刪除：該刪除為跨使用者的硬刪（篩選條件不含 `line_user_id`、不留 tombstone），一旦由使用者可控的 URL 觸發，任一登入使用者即可藉由送出他人回報中的 URL 使其永久消失。手動路徑的重複 URL SHALL 由審核端目視處理。

#### Scenario: pending 同 URL 刪舊留新

- **WHEN** 新回報將包含 URL A，且已有 pending 回報也含 URL A
- **THEN** 舊回報被刪除，僅保留新建回報

#### Scenario: 手動回報不刪除他人回報

- **WHEN** 使用者手動送出的 URL 與另一使用者既有 pending 回報的 URL 相同
- **THEN** 兩筆回報並存，既有回報 SHALL NOT 被刪除

## ADDED Requirements

### Requirement: 回報來源標記

每筆知識回報 SHALL 記錄其建立來源（手動表單／agent tool／web fallback）。本欄位加入前既有的紀錄無此欄位，系統 SHALL 將其視為非手動來源。

來源標記 SHALL 只用於配額計數與審核端呈現，SHALL NOT 影響審核流程、ingest 行為或去重判斷。

#### Scenario: 手動回報標記為手動

- **WHEN** 使用者透過建立端點送出回報
- **THEN** 該回報的來源標記為手動

#### Scenario: 自動回報不標記為手動

- **WHEN** web fallback 自動建立回報
- **THEN** 該回報的來源標記為 web fallback，且不計入任何使用者的手動配額

#### Scenario: 舊紀錄視為非手動

- **WHEN** 讀取本需求生效前寫入、無來源欄位的回報
- **THEN** 系統視其為非手動來源，SHALL NOT 因此拒絕存取或計入配額

### Requirement: 手動回報配額

使用者端建立端點 SHALL 對每個 `line_user_id` 施加手動回報數量配額：滾動時間視窗（24 小時）內手動來源的回報數達到可設定的上限時，SHALL 以 429 拒絕新的建立請求，並在回應中提供可判讀的錯誤碼與上限值，使呼叫端能組出含次數的提示。

配額 SHALL 只計手動來源的回報。agent tool 與 web fallback 建立的回報 SHALL NOT 計入，亦 SHALL NOT 因配額而被拒——否則使用者的正常提問會消耗其手動回報額度。

視窗 SHALL 為滾動視窗而非自然日，避免跨午夜時可送出兩倍數量。

#### Scenario: 達上限後拒絕

- **WHEN** 使用者在 24 小時內手動回報數已達上限並再次送出
- **THEN** 回傳 429 與可判讀的配額錯誤碼及上限值，不建立回報

#### Scenario: 自動回報不佔額度

- **WHEN** 使用者的 web fallback 自動回報筆數已超過上限，但手動回報筆數為零
- **THEN** 該使用者仍可成功建立手動回報

#### Scenario: 自動路徑不受配額限制

- **WHEN** web fallback 為配額已滿的使用者建立自動回報
- **THEN** 自動回報照常建立，SHALL NOT 因配額被拒

### Requirement: 回報編號碰撞重試

`report_id` 的隨機後綴長度有限，`report_id` 具唯一索引。系統 SHALL 在寫入遭遇唯一鍵衝突時重新產生編號並重試，重試次數 SHALL 有上限；SHALL NOT 讓唯一鍵衝突以未處理例外的形式回傳給呼叫端。

此需求的目的是避免編號碰撞在使用者端呈現為伺服器錯誤——該錯誤會出現在剛送出表單的畫面上，並被誤讀為「網址被白名單擋下」。

#### Scenario: 碰撞後換編號成功

- **WHEN** 第一次寫入因 `report_id` 已存在而失敗
- **THEN** 系統以新的 `report_id` 重試並成功建立回報，呼叫端收到成功回應

#### Scenario: 重試耗盡才失敗

- **WHEN** 連續多次寫入皆因唯一鍵衝突失敗且已達重試上限
- **THEN** 系統回傳錯誤，SHALL NOT 無上限重試
