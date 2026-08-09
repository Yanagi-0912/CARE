## 1. Repository / Service：去重與自動建報

- [x] 1.1 `KnowledgeReportRepository`：新增依 URL 刪除 `pending`/`reviewing` 回報（`user_source_urls` 含該 URL）
- [x] 1.2 `KnowledgeReportService.create_from_web_fallback(question, urls, line_user_id)`：去重後建立 pending／reason=missing／user_note=`auto:web-fallback`
- [x] 1.3 `approve`：`selected_urls` 可省略／空 → 回退 `user_source_urls`；皆空則 400
- [x] 1.4 單元測試：去重、create_from_web_fallback、approve 省略 URL

## 2. Admin 列表 API

- [x] 2.1 `GET /api/admin/knowledge-reports`（可選 status query；預設 pending+reviewing）
- [x] 2.2 Repo／service list_for_admin；router 測試（admin／非 admin）

## 3. Web fallback 串接

- [x] 3.1 DI：將 knowledge report 建報能力注入 `WebSearchService`（或 callback）
- [x] 3.2 成功回答後用 `get_line_user_id` + 引用 URL 呼叫建報；失敗僅 log
- [x] 3.3 單元測試：成功建報、失敗路徑不建、無 user id 略過

## 4. 驗收

- [x] 4.1 相關 pytest 全綠
- [x] 4.2 勾核 openspec tasks
