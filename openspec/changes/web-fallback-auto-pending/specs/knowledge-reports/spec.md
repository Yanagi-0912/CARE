## ADDED Requirements

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
