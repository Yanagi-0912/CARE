# knowledge-reports Specification

## Purpose
TBD - created by archiving change knowledge-reports. Update Purpose after archive.
## Requirements
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

### Requirement: 核准後自動 ingest

營運端核准或拒絕回報時，系統 SHALL 要求呼叫者為已登入且 `role=admin` 的使用者（Bearer JWT）。

核准時系統 SHALL 先同步完成驗證（回報存在、狀態可核准、選定 URL 全部通過 `url-trust` 定義的正規化與允許網域檢查、且通過預覽快照綁定驗證），驗證失敗 SHALL 以既有錯誤碼拒絕且不改動任何狀態。

URL 驗證 SHALL 一次檢查全部選定 URL，並在失敗回應中列出**所有**不合格的 URL 與各自原因（`malformed`／`not_allowed`），SHALL NOT 只回報第一個不合格項。失敗回應的內容 SHALL 為結構化資料（含錯誤碼、不合格 URL 清單與可直接顯示的訊息），使審核介面 SHALL 能逐項標示是哪些 URL 有問題、問題為何。錯誤訊息 SHALL 取自訊息目錄，SHALL NOT 於服務層硬編英文字串。

驗證通過後，排入 ingest 的目標 SHALL 為正規化後的 URL，SHALL NOT 為呼叫端送出的原始字串。系統 SHALL 立即回應，將回報標記為 `reviewing` 且 `ingest_job.status=running`，並於回應送出後才執行收錄。核准端點 SHALL NOT 在 HTTP 回應中等待 ingest 完成，因此 SHALL NOT 於核准回應直接回傳 `resolved`。

背景收錄 SHALL 使用核准時所綁定的預覽快照內容，SHALL NOT 重新抓取該 URL。快照在收錄前已不可用時，該 URL SHALL 視為失敗，SHALL NOT 改以重新抓取的內容替代。

背景 ingest 全部成功時系統 SHALL 將 `status` 設為 `resolved` 且 `ingest_job.status=succeeded`；任一 URL 失敗時 SHALL NOT 標記 `resolved`，SHALL 將 `ingest_job.status` 設為 `failed` 並記錄可讀的錯誤訊息。背景執行過程發生未預期例外時，系統 SHALL 仍將 `ingest_job.status` 收斂為 `failed`，SHALL NOT 讓工作停留在 `running`。

#### Scenario: admin 核准立即回應

- **WHEN** role=admin 的使用者以有效 Bearer token 核准並提供允許網域 URL 與有效的預覽綁定
- **THEN** 回應立即回傳該回報，`status` 為 `reviewing` 且 `ingest_job.status` 為 `running`，ingest 尚未在回應中完成

#### Scenario: 背景 ingest 全部成功

- **WHEN** 背景工作對全部選定 URL 收錄成功
- **THEN** 回報 `status` 為 `resolved`、`ingest_job.status` 為 `succeeded`，且向量庫含該 URL 的 chunk

#### Scenario: 背景收錄不重新抓取

- **WHEN** 背景工作開始收錄一個已通過預覽綁定驗證的 URL
- **THEN** 系統以快照內容切塊寫入，SHALL NOT 對該 URL 發出新的抓取請求

#### Scenario: ingest 失敗不假 resolved

- **WHEN** 背景工作中任一 URL 收錄失敗
- **THEN** `status` 不為 `resolved`，`ingest_job.status` 為 `failed`，並可查得錯誤訊息

#### Scenario: 非白名單 URL 於回應階段即拒絕

- **WHEN** 核准請求包含非白名單 URL
- **THEN** 回傳 400，回報狀態不變且不排入背景工作

#### Scenario: 多個不合格 URL 一次列出

- **WHEN** 核准請求包含兩個不合格 URL（一個格式不合法、一個網域不在允許清單）
- **THEN** 400 回應同時列出兩者與各自原因，回報狀態不變且不排入背景工作

#### Scenario: 看似政府網址的偽裝 URL 被拒絕

- **WHEN** 核准請求包含以反斜線偽裝成允許網域的 URL（例如 `https://evil.com\.gov.tw/page`）
- **THEN** 回傳 400，該 URL 不進入 ingest

#### Scenario: 正規化後的 URL 才進入 ingest

- **WHEN** 核准請求送出帶有追蹤參數或大寫主機名的合法 URL
- **THEN** 登記的 `ingest_job.selected_urls` 為正規化後的字串，背景工作以該字串綁定預覽快照

#### Scenario: 非 admin 無法核准

- **WHEN** role 非 admin 的已登入使用者呼叫核准端點
- **THEN** 回傳 403，不執行 ingest

#### Scenario: 拒絕回報

- **WHEN** 營運拒絕回報
- **THEN** status 為 rejected，不執行 ingest

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

### Requirement: Web fallback 自動建立 pending 回報

當 CRAG／知識庫不足而 web fallback **成功**回答，且回答引用至少一個白名單來源 URL 時，系統 SHALL 自動建立一筆 `KnowledgeReport`：`status=pending`、`reason=missing`、`question` 為該次查詢、`user_source_urls` 為本次引用 URL（最多 3 個）。Web 失敗、無可用來源、或未走 web fallback 時，SHALL NOT 因此建立回報。建立失敗 SHALL NOT 阻斷使用者回答。

#### Scenario: 成功網路回答建立 pending

- **WHEN** web fallback 成功並附上白名單來源 URL
- **THEN** 系統建立 status=pending、reason=missing 的知識回報，且 `user_source_urls` 含該些 URL

#### Scenario: 網路失敗不建報

- **WHEN** 觸發 web fallback 但無可用頁面、服務錯誤或模型無法回答
- **THEN** 系統不建立知識回報

### Requirement: 同 URL 待審回報去重

建立 **web fallback 自動回報** 前，若既有 `pending` 或 `reviewing` 回報的 `user_source_urls` 含任一即將寫入的 URL，系統 SHALL 刪除該舊回報，再建立新回報。

此刪除 SHALL 只適用於自動建報路徑。使用者手動建立的回報 SHALL NOT 觸發任何刪除：該刪除為跨使用者的硬刪（篩選條件不含 `line_user_id`、不留 tombstone），一旦由使用者可控的 URL 觸發，任一登入使用者即可藉由送出他人回報中的 URL 使其永久消失。手動路徑的重複 URL SHALL 由審核端目視處理。

#### Scenario: pending 同 URL 刪舊留新

- **WHEN** 新回報將包含 URL A，且已有 pending 回報也含 URL A
- **THEN** 舊回報被刪除，僅保留新建回報

#### Scenario: 手動回報不刪除他人回報

- **WHEN** 使用者手動送出的 URL 與另一使用者既有 pending 回報的 URL 相同
- **THEN** 兩筆回報並存，既有回報 SHALL NOT 被刪除

### Requirement: 核准可省略 selected_urls

營運核准回報時，若未提供 `selected_urls`（省略或空列表），系統 SHALL 使用該回報的 `user_source_urls` 作為 ingest 目標。目標列表仍不得為空，且每個 URL MUST 通過白名單；否則拒絕核准。全部 ingest 成功後 status 為 resolved；同 URL 入庫覆蓋行為沿用 `IngestService`。

#### Scenario: 省略 URL 以報告來源核准

- **WHEN** 營運核准一筆含白名單 `user_source_urls` 的回報且未傳 selected_urls
- **THEN** 系統對報告上的 URL 執行 ingest，成功後 status 為 resolved

#### Scenario: 無可用 URL 拒絕核准

- **WHEN** 核准時 selected_urls 與 user_source_urls 皆無有效 URL
- **THEN** 系統回傳 400 且不執行 ingest

### Requirement: Admin 列出待審回報

系統 SHALL 提供需 admin 身分的 `GET /api/admin/knowledge-reports`，回傳 `pending` 與／或 `reviewing` 回報（可依 query 篩選），依建立時間新到舊排序。

#### Scenario: Admin 取得 pending 佇列

- **WHEN** 已驗證 admin 請求待審列表
- **THEN** 回傳符合狀態篩選的回報，不含其他使用者私有列表限制以外的未授權存取

### Requirement: ingest 工作狀態與重試

每筆核准產生的 ingest 工作 SHALL 記錄執行狀態（`running`／`succeeded`／`failed`）與起訖時間，使營運端能區分「進行中」與「已結束但失敗」。本需求生效前既有的紀錄無執行狀態，系統 SHALL 將其視為已結束。

失敗的 ingest SHALL 可透過再次呼叫同一支核准端點重試，系統 SHALL NOT 為此另設端點。系統 SHALL 拒絕對同一回報重複啟動仍在進行中的工作。若進行中的工作已超過逾時門檻（視為服務重啟遺留），系統 SHALL 允許重新核准以取代該工作。

#### Scenario: 重試失敗的 ingest

- **WHEN** admin 對 `ingest_job.status=failed` 的 `reviewing` 回報再次呼叫核准
- **THEN** 系統以新的選定 URL 重新啟動 ingest，覆寫前次工作紀錄

#### Scenario: 拒絕重複啟動

- **WHEN** admin 對 `ingest_job.status=running` 且未逾時的回報再次呼叫核准
- **THEN** 回傳 409，不啟動第二份工作

#### Scenario: 逾時的孤兒工作可重跑

- **WHEN** 回報的 `ingest_job.status=running` 但開始時間已超過逾時門檻
- **THEN** 系統允許重新核准並啟動新的 ingest 工作

#### Scenario: 舊紀錄不阻擋重試

- **WHEN** 回報的 `ingest_job` 無執行狀態欄位（本需求生效前寫入）
- **THEN** 系統視為已結束，允許重新核准

### Requirement: Admin 待審列表的參數驗證與分頁

Admin 待審列表端點的 `status` 參數 SHALL 僅接受合法的回報狀態值（`pending`／`reviewing`／`resolved`／`rejected`）；非法值 SHALL 以 422 拒絕，SHALL NOT 以空列表回應。

該端點 SHALL 支援分頁：`limit` 預設 50、下限 1、上限 200，超出範圍 SHALL 以 422 拒絕；`offset` 預設 0。回應 SHALL 除回報列表外一併提供符合篩選條件的總筆數與本次的 `limit`／`offset`，使呼叫端能判斷是否還有未載入的資料。未帶分頁參數的呼叫 SHALL 回傳第一頁。

使用者端的個人回報列表 SHALL NOT 受本需求影響。

#### Scenario: 非法 status 回 422

- **WHEN** admin 以 `status=foo` 查詢待審列表
- **THEN** 回傳 422，不查詢資料庫

#### Scenario: 預設分頁

- **WHEN** admin 未帶分頁參數查詢待審列表
- **THEN** 回傳最多 50 筆，並附上符合條件的總筆數與 `limit=50`／`offset=0`

#### Scenario: 指定分頁位移

- **WHEN** admin 以 `limit=20&offset=20` 查詢
- **THEN** 回傳依建立時間新到舊排序的第 21～40 筆，總筆數不受分頁影響

#### Scenario: 超出上限的 limit

- **WHEN** admin 以 `limit=500` 查詢
- **THEN** 回傳 422

### Requirement: 背景 ingest 不得覆蓋期間內的其他變更

背景 ingest 寫回結果時，系統 SHALL 僅在該回報的 ingest 工作仍是本次啟動的那一份時才套用。若期間該回報已被拒絕或已被重新核准而啟動新工作，系統 SHALL 丟棄本次結果，SHALL NOT 覆寫回報的狀態、審核備註或新工作的紀錄。

背景 ingest 寫回時 SHALL 僅更新與該次 ingest 相關的欄位，SHALL NOT 以工作開始時的整份快照覆寫回報。

#### Scenario: 拒絕不被背景工作還原

- **WHEN** 回報於 ingest 進行中被拒絕，而該 ingest 隨後完成
- **THEN** 回報維持 `rejected`，拒絕時寫入的審核備註保留，ingest 結果不被套用

#### Scenario: 重新核准後舊工作的結果不生效

- **WHEN** 回報的 ingest 逾時後被重新核准並啟動新工作，稍後舊工作才完成
- **THEN** 回報反映新工作的紀錄，舊工作的結果被丟棄

### Requirement: 拒絕與進行中的 ingest 互斥

系統 SHALL 拒絕在 ingest 進行中對該回報執行拒絕，並以 409 回應。已逾時的進行中工作 SHALL NOT 阻擋拒絕。

此限制的目的是避免出現「回報已拒絕，但其來源內容已進入向量庫」的狀態；系統不具備反收錄能力。

#### Scenario: ingest 進行中不得拒絕

- **WHEN** admin 對 `ingest_job.status=running` 且未逾時的回報呼叫拒絕
- **THEN** 回傳 409，回報狀態不變

#### Scenario: 逾時後可拒絕

- **WHEN** 回報的進行中工作已超過逾時門檻
- **THEN** 拒絕正常執行

### Requirement: 核准不得清除既有審核備註

核准請求未帶 `resolution`／`reviewer_note` 時，系統 SHALL 保留回報上既有的值，SHALL NOT 將其清為空值。帶值時 SHALL 覆寫。

#### Scenario: 重試保留前次備註

- **WHEN** admin 對先前已寫入審核備註的回報重試核准，且本次未填備註
- **THEN** 既有審核備註保留不變

#### Scenario: 帶值時覆寫

- **WHEN** 核准請求帶有新的審核備註
- **THEN** 以新值取代既有值

### Requirement: 併發核准不得重複啟動 ingest

系統 SHALL 以原子操作登記 ingest 工作，使同一回報在併發核准下 SHALL NOT 啟動一份以上的背景工作。未取得登記的請求 SHALL 以 409 回應。

#### Scenario: 併發核准只有一個成功

- **WHEN** 兩個核准請求同時對同一筆 pending 回報送出
- **THEN** 僅其中一個啟動 ingest，另一個回傳 409

### Requirement: 待審列表回傳各狀態實際筆數

Admin 待審列表 SHALL 於回應中一併提供 `pending` 與 `reviewing` 的實際筆數，且 SHALL NOT 受本次查詢的 `status` 篩選或分頁參數影響，使呼叫端 SHALL NOT 需要以已載入的資料自行推算佇列規模。

#### Scenario: 篩選時仍回傳完整計數

- **WHEN** admin 以 `status=pending` 查詢待審列表
- **THEN** 回應同時包含 pending 與 reviewing 的實際筆數，reviewing 的筆數不因篩選而為零

### Requirement: 待審查詢的索引支援

系統 SHALL 為依狀態篩選並依建立時間排序的待審查詢建立對應索引，使該查詢 SHALL NOT 依賴全集合掃描與記憶體排序。

#### Scenario: 建立索引

- **WHEN** 系統初始化知識回報集合的索引
- **THEN** 存在涵蓋 `status` 與 `created_at` 的複合索引

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

### Requirement: 核准前的內容預覽

系統 SHALL 提供需 admin 身分的內容預覽資源，使營運端能在核准前看到將被收錄的實際內容。

啟動預覽時系統 SHALL 先同步驗證：回報存在、目標 URL 經正規化後全部通過白名單、數量未超過上限。驗證失敗 SHALL 以既有錯誤碼拒絕且不進行任何抓取；白名單驗證 SHALL 一次回報全部不合格的 URL，SHALL NOT 只回報第一個。

驗證通過後系統 SHALL 立即回應並標示預覽為進行中，實際抓取 SHALL 於回應送出後才執行。預覽端點 SHALL NOT 在 HTTP 回應中等待外部抓取服務完成。

抓取結果 SHALL 逐 URL 記錄狀態（成功／空內容／失敗）、頁面標題、字數與內容雜湊，並將抓取到的原文快照保存於伺服器端。抓取過程發生未預期例外時，系統 SHALL 仍將預覽狀態收斂為失敗，SHALL NOT 讓其停留在進行中。

回傳給呼叫端的內容 SHALL 是抓取到的原文（超過長度上限時截斷並標示已截斷），SHALL NOT 以模型摘要取代原文——否則核准的對象與收錄的對象不是同一份文字。

#### Scenario: 啟動預覽立即回應

- **WHEN** admin 對一筆回報的白名單 URL 啟動預覽
- **THEN** 端點立即回應且預覽狀態為進行中，抓取尚未在回應中完成

#### Scenario: 非白名單 URL 於啟動階段即拒絕

- **WHEN** 啟動預覽的請求包含非白名單 URL
- **THEN** 回傳 400 且不進行任何抓取，回應列出全部不合格的 URL

#### Scenario: 抓取完成可取得內容

- **WHEN** 背景抓取完成且至少一個 URL 成功
- **THEN** 查詢預覽可取得逐 URL 的狀態、標題、字數、內容雜湊與原文

#### Scenario: 抓取失敗不停在進行中

- **WHEN** 抓取過程中外部服務失敗或拋出例外
- **THEN** 預覽狀態收斂為失敗並可查得錯誤訊息，SHALL NOT 停留在進行中

### Requirement: 預覽快照的保存期限與取代

預覽快照 SHALL 有保存期限，逾期後 SHALL 自動失效且 SHALL NOT 再作為核准依據。

同一筆回報 SHALL 只保留最新一份預覽。以不同的 URL 集合或明確要求重新抓取而啟動的預覽 SHALL 取代既有預覽並取得新的預覽識別碼，舊識別碼 SHALL 隨即失效。

當該回報已有未逾期、URL 集合相同且已就緒的預覽時，再次啟動預覽 SHALL 直接回傳既有預覽而 SHALL NOT 重新抓取，除非請求明確要求重新抓取。此規則避免瀏覽佇列時對外部抓取服務產生重複請求。

#### Scenario: 逾期預覽不可用

- **WHEN** 預覽建立時間已超過保存期限
- **THEN** 查詢預覽回傳查無資料，且該預覽不能用於核准

#### Scenario: 重新抓取取代舊預覽

- **WHEN** admin 對同一回報要求重新抓取
- **THEN** 系統產生新的預覽識別碼並取代舊預覽，舊識別碼失效

#### Scenario: 期限內不重複抓取

- **WHEN** admin 再次開啟一筆已有未逾期且已就緒預覽的回報，URL 集合未變且未要求重新抓取
- **THEN** 系統回傳既有預覽，不對外部服務發出新的抓取請求

### Requirement: 核准綁定預覽快照

核准 SHALL 綁定一份具體的預覽快照，使核准的對象是「這份內容」而非「這個網址」。

核准請求 SHALL 指明所依據的預覽識別碼，並對每個選定 URL 指明呼叫端所檢視內容的雜湊值。系統 SHALL 驗證：該預覽是這筆回報最新且未逾期的一份、每個選定 URL 在該預覽中且抓取成功、且其內容雜湊與請求指明的值相符。任一項不成立時系統 SHALL 以 409 拒絕核准，SHALL NOT 改動回報狀態，並 SHALL 在錯誤訊息中區分「預覽逾期」「預覽已被取代」與「內容雜湊不符」。

此需求的目的是消除「檢查的時刻」與「抓取的時刻」之間的落差：驗證通過即代表呼叫端已檢視過的位元組與後續寫入向量庫的位元組是同一份。

#### Scenario: 雜湊不符拒絕核准

- **WHEN** 核准請求指明的內容雜湊與伺服器端快照不符
- **THEN** 回傳 409，回報狀態不變且不排入 ingest

#### Scenario: 預覽逾期拒絕核准

- **WHEN** 核准所依據的預覽已超過保存期限
- **THEN** 回傳 409 並指出預覽已逾期，回報狀態不變

#### Scenario: 選定 URL 不在預覽中

- **WHEN** 核准的選定 URL 未包含於該預覽，或其在預覽中的抓取狀態不是成功
- **THEN** 回傳 409，回報狀態不變

### Requirement: 重新收錄不得清除既有來源名稱

對同一 URL 重新收錄時，若呼叫端未指定來源名稱，系統 SHALL 沿用該 URL 既有文件的來源名稱，SHALL NOT 將其寫成空值。此讀取 SHALL 發生在刪除既有文件之前。

呼叫端已指定來源名稱時 SHALL 以指定值寫入。既有文件不存在或其來源名稱為空時，系統 SHALL 使用呼叫端提供的預設值（例如抓取到的頁面標題），使新收錄的內容仍具備可讀的來源名稱。

此需求的存在理由：「這頁資料已過時」是最常見的回報原因，其處理路徑正是對既有策展 URL 重新收錄；來源名稱一旦被清空，該來源在回答的參考清單與檢索上下文標頭中就只剩網址。

#### Scenario: 沿用既有來源名稱

- **WHEN** 對一個向量庫中已有文件且具來源名稱的 URL 重新收錄，且未指定來源名稱
- **THEN** 新寫入的文件保留原本的來源名稱

#### Scenario: 新 URL 使用提供的預設名稱

- **WHEN** 對向量庫中尚無文件的 URL 收錄，且呼叫端提供了來源名稱
- **THEN** 新寫入的文件使用該名稱，而非空字串

