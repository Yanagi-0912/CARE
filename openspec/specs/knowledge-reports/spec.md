# knowledge-reports Specification

## Purpose
TBD - created by archiving change knowledge-reports. Update Purpose after archive.
## Requirements
### Requirement: 建立與查詢知識回報

系統 SHALL 將知識回報持久化於 MongoDB，並允許已登入使用者建立回報與查詢自己的回報列表。每筆回報 SHALL 含唯一 `report_id`、`line_user_id`、`status`（pending／reviewing／resolved／rejected）、`reason`、`question`，以及可選的補充說明與來源 URL 列表。

#### Scenario: 使用者建立回報

- **WHEN** 已驗證使用者提交 question 與合法 reason
- **THEN** 系統建立 status=pending 的回報並回傳 report_id

#### Scenario: 使用者列出自己的回報

- **WHEN** 已驗證使用者請求列表
- **THEN** 僅回傳該使用者的回報，依建立時間新到舊

### Requirement: 核准後自動 ingest

營運端核准或拒絕回報時，系統 SHALL 要求呼叫者為已登入且 `role=admin` 的使用者（Bearer JWT）。

核准時系統 SHALL 先同步完成驗證（回報存在、狀態可核准、選定 URL 全部通過 `url-trust` 定義的正規化與允許網域檢查），驗證失敗 SHALL 以既有錯誤碼拒絕且不改動任何狀態。

URL 驗證 SHALL 一次檢查全部選定 URL，並在失敗回應中列出**所有**不合格的 URL 與各自原因（`malformed`／`not_allowed`），SHALL NOT 只回報第一個不合格項。失敗回應的內容 SHALL 為結構化資料（含錯誤碼、不合格 URL 清單與可直接顯示的訊息），使審核介面 SHALL 能逐項標示是哪些 URL 有問題、問題為何。錯誤訊息 SHALL 取自訊息目錄，SHALL NOT 於服務層硬編英文字串。

驗證通過後，排入 ingest 的目標 SHALL 為正規化後的 URL，SHALL NOT 為呼叫端送出的原始字串。系統 SHALL 立即回應，將回報標記為 `reviewing` 且 `ingest_job.status=running`，並於回應送出後才對選定 URL 呼叫 `IngestService.ingest_url`。核准端點 SHALL NOT 在 HTTP 回應中等待 ingest 完成，因此 SHALL NOT 於核准回應直接回傳 `resolved`。

背景 ingest 全部成功時系統 SHALL 將 `status` 設為 `resolved` 且 `ingest_job.status=succeeded`；任一 URL 失敗時 SHALL NOT 標記 `resolved`，SHALL 將 `ingest_job.status` 設為 `failed` 並記錄可讀的錯誤訊息。背景執行過程發生未預期例外時，系統 SHALL 仍將 `ingest_job.status` 收斂為 `failed`，SHALL NOT 讓工作停留在 `running`。

#### Scenario: admin 核准立即回應

- **WHEN** role=admin 的使用者以有效 Bearer token 核准並提供允許網域 URL
- **THEN** 回應立即回傳該回報，`status` 為 `reviewing` 且 `ingest_job.status` 為 `running`，ingest 尚未在回應中完成

#### Scenario: 背景 ingest 全部成功

- **WHEN** 背景工作對全部選定 URL ingest 成功
- **THEN** 回報 `status` 為 `resolved`、`ingest_job.status` 為 `succeeded`，且向量庫含該 URL 的 chunk

#### Scenario: ingest 失敗不假 resolved

- **WHEN** 背景工作中任一 URL ingest 失敗
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
- **THEN** 登記的 `ingest_job.selected_urls` 為正規化後的字串，背景工作以該字串抓取與寫入

#### Scenario: 非 admin 無法核准

- **WHEN** role 非 admin 的已登入使用者呼叫核准端點
- **THEN** 回傳 403，不執行 ingest

#### Scenario: 拒絕回報

- **WHEN** 營運拒絕回報
- **THEN** status 為 rejected，不執行 ingest

### Requirement: Agent 可提交回報

系統 SHALL 提供 Agent tool `submit_knowledge_report`，在已知 line_user_id 的對話脈絡下建立 pending 回報。

#### Scenario: Tool 建立 pending

- **WHEN** Agent 呼叫 submit_knowledge_report 且脈絡有 line_user_id
- **THEN** 建立 pending 回報並回傳 report_id 摘要

### Requirement: Web fallback 自動建立 pending 回報

當 CRAG／知識庫不足而 web fallback **成功**回答，且回答引用至少一個白名單來源 URL 時，系統 SHALL 自動建立一筆 `KnowledgeReport`：`status=pending`、`reason=missing`、`question` 為該次查詢、`user_source_urls` 為本次引用 URL（最多 3 個）。Web 失敗、無可用來源、或未走 web fallback 時，SHALL NOT 因此建立回報。建立失敗 SHALL NOT 阻斷使用者回答。

#### Scenario: 成功網路回答建立 pending

- **WHEN** web fallback 成功並附上白名單來源 URL
- **THEN** 系統建立 status=pending、reason=missing 的知識回報，且 `user_source_urls` 含該些 URL

#### Scenario: 網路失敗不建報

- **WHEN** 觸發 web fallback 但無可用頁面、服務錯誤或模型無法回答
- **THEN** 系統不建立知識回報

### Requirement: 同 URL 待審回報去重

建立自動（或一般）回報前，若既有 `pending` 或 `reviewing` 回報的 `user_source_urls` 含任一即將寫入的 URL，系統 SHALL 刪除該舊回報，再建立新回報。

#### Scenario: pending 同 URL 刪舊留新

- **WHEN** 新回報將包含 URL A，且已有 pending 回報也含 URL A
- **THEN** 舊回報被刪除，僅保留新建回報

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

