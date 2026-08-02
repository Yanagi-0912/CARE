## Why

知識回報 LIFF 僅有 mock；沒有後端狀態機，也無法在人工核准後自動呼叫既有 `IngestService` 入庫。要讓審查成為流程內狀態，並接上白名單網頁 ingest。

## What Changes

- Mongo `knowledge_reports` collection＋repository／service（pending→reviewing→resolved／rejected）。
- 使用者 API：建立回報、列出自己的回報（JWT）。
- 營運 API：approve（選定白名單 URL→`IngestService.ingest_url`）／reject（`X-Admin-Key`）。
- Agent tool `submit_knowledge_report`。
- CARE-LIFF KnowledgeReports 改打真 API（去 mock）。
- **不做**：PDF 解析、完整 Admin UI、自動無審核寫庫。

## Capabilities

### New Capabilities

- `knowledge-reports`：回報生命週期、核准入庫、使用者列表。

### Modified Capabilities

- `agent-architecture`：工具集可含 `submit_knowledge_report`。

## Impact

- CARE：`app/db`、`repositories`、`services`、`routers`、`tools`、`dependencies`、`config`／`.env.example`
- CARE-LIFF：`KnowledgeReports` 頁＋`knowledgeReportsApi.ts`
- 設定：`KNOWLEDGE_REPORTS_ADMIN_API_KEY`（approve／reject）
