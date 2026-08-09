## MODIFIED Requirements

### Requirement: 核准後自動 ingest

營運端核准或拒絕回報時，系統 SHALL 要求呼叫者為已登入且 `role=admin` 的使用者（Bearer JWT）。

核准時系統 SHALL 先同步完成驗證（回報存在、狀態可核准、選定 URL 全部通過白名單），驗證失敗 SHALL 以既有錯誤碼拒絕且不改動任何狀態。驗證通過後系統 SHALL 立即回應，將回報標記為 `reviewing` 且 `ingest_job.status=running`，並於回應送出後才對選定 URL 呼叫 `IngestService.ingest_url`。核准端點 SHALL NOT 在 HTTP 回應中等待 ingest 完成，因此 SHALL NOT 於核准回應直接回傳 `resolved`。

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

#### Scenario: 非 admin 無法核准

- **WHEN** role 非 admin 的已登入使用者呼叫核准端點
- **THEN** 回傳 403，不執行 ingest

#### Scenario: 拒絕回報

- **WHEN** 營運拒絕回報
- **THEN** status 為 rejected，不執行 ingest

## ADDED Requirements

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
