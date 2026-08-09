## Why

Admin 審核頁與 admin API 上線後，累積了四個已知缺口：

1. **無法挑選來源 URL**：後端 `approve` 支援 `selected_urls`，但前端只送 `reviewer_note`，等於一律全收 `user_source_urls`。回報附了三個 URL 但只有一個可信時，admin 沒有辦法只收那一個。
2. **看不到 ingest 失敗原因**：前端 `KnowledgeReportDto` 沒有 `ingest_job` 欄位。ingest 失敗的回報留在 `reviewing`，在畫面上與「還沒審」長得一模一樣，admin 不知道發生什麼事、也沒有重試入口。
3. **`status` query 不驗證**：`?status=foo` 會原封不動查 Mongo 並回空陣列，看起來像「佇列是空的」，而不是 422 參數錯誤。
4. **列表無分頁**：`list_by_statuses` 用 `to_list(length=None)` 全撈，回報累積後 admin 頁一次載入全部。

## What Changes

- **後端**
  - `GET /api/admin/knowledge-reports` 的 `status` 改為 `KnowledgeReportStatus` 型別，非法值由 FastAPI 回 422
  - 同端點新增 `limit`（預設 50，上限 200）與 `offset` 分頁參數；回應新增 `total`／`limit`／`offset`
  - repository 新增 `count_by_statuses`，`list_by_statuses` 支援 limit／offset
- **前端（CARE-LIFF）**
  - `KnowledgeReportDto` 補上 `ingest_job` 型別；列表與詳情顯示 ingest 狀態，`failed` 顯示錯誤訊息與逐 URL 結果
  - 詳情 dialog 將 `user_source_urls` 改為可勾選（預設全選、至少選一個才能核准），核准時送 `selected_urls`
  - ingest 失敗的回報提供「重試」動作（沿用 approve）
  - 佇列支援分頁載入

## Capabilities

### Modified Capabilities

- `knowledge-reports`：admin 列表端點的參數驗證與分頁契約
- `admin-knowledge-reports-ui`：審核頁的 URL 挑選、ingest 狀態顯示與重試

## Impact

- **CARE**：`app/routers/admin/knowledge_reports.py`、`app/models/knowledge_report.py`、`app/repositories/knowledge_report_repository.py`
- **CARE-LIFF**：`src/api/knowledgeReportsApi.ts`、`src/pages/AdminKnowledgeReports/index.tsx`、`src/i18n/adminKnowledgeMessages.ts`、`src/tests/adminKnowledgeReports.test.tsx`
- **測試**：`tests/unit/routers/test_knowledge_reports.py`、`tests/unit/repositories/test_knowledge_report_repository.py`；LIFF vitest
- **相依**：ingest 狀態顯示與重試依賴 `knowledge-report-async-ingest` 定義的 `ingest_job.status`，該 change 需先完成
- **相容**：`KnowledgeReportListResponse` 的新欄位皆 optional，使用者端 `GET /api/knowledge-reports` 行為不變
