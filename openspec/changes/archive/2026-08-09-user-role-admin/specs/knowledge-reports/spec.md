## MODIFIED Requirements

### Requirement: 核准後自動 ingest

營運端核准或拒絕回報時，系統 SHALL 要求呼叫者為已登入且 `role=admin` 的使用者（Bearer JWT）。系統 SHALL NOT 再以共享 admin API key（`X-Admin-Key`）作為核准依據。核准時系統 SHALL 對選定的白名單 URL 呼叫既有 `IngestService.ingest_url`。僅當全部選定 URL 入庫成功時，SHALL 將 status 設為 resolved；任一失敗 SHALL NOT 標記 resolved，並記錄 ingest 錯誤資訊。非白名單 URL SHALL 拒絕核准。

#### Scenario: admin JWT 核准成功

- **WHEN** role=admin 的使用者以有效 Bearer token 核准並提供允許網域 URL，且 ingest 全部成功
- **THEN** 回報 status 為 resolved

#### Scenario: 非 admin 無法核准

- **WHEN** role 非 admin 的已登入使用者呼叫核准端點
- **THEN** 回傳 403，不執行 ingest
