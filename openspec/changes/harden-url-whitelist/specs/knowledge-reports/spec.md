## MODIFIED Requirements

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
