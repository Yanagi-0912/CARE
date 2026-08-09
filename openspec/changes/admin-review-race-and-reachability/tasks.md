## 1. 後端：條件式更新

- [x] 1.1 repository 新增 `start_ingest_job`：filter 表達「未結案」＋「無進行中的新鮮工作」，回傳是否命中
- [x] 1.2 repository 新增 `finish_ingest_job`：filter 綁 `ingest_job.started_at` 與 `status=running`，只寫 ingest 相關欄位
- [x] 1.3 `approve` 改用 `start_ingest_job`，未命中回 409
- [x] 1.4 `run_ingest` 改用 `finish_ingest_job`，未命中即丟棄結果

## 2. 後端：其餘修正

- [x] 2.1 `reject` 於 ingest 進行中回 409（沿用 `_is_ingest_in_progress`）
- [x] 2.2 `approve` 的 `resolution`／`reviewer_note` 改 patch 語意（None 保留原值）
- [x] 2.3 `run_ingest` 例外路徑保留已收集的 `results`
- [x] 2.4 `ensure_indexes` 新增 `status` + `created_at` 複合索引

## 3. 前端

- [x] 3.1 篩選改 server-side：`activeFilter` 進 query key 並帶 `status`；移除 client-side `visibleReports`
- [x] 3.2 頁籤與 hero 統計改用後端 `status_counts`（實作時發現只顯示選中頁籤會讓 stats 失真，改由後端回各狀態筆數）
- [x] 3.3 `openDialog` 優先以 `ingest_job.selected_urls` 種入選取，非使用者提供者歸入 `extraUrls`
- [x] 3.4 有進行中 ingest 時 `refetchInterval` 5 秒，否則 false
- [x] 3.5 flatten 分頁結果時依 `report_id` 去重

## 4. 測試

- [x] 4.1 `tests/unit/repositories/test_knowledge_report_repository.py`：兩個條件式更新的 filter 與命中／未命中
- [x] 4.2 `tests/unit/services/knowledge_reports/test_service.py`：ingest 中拒絕回 409；逾時後可拒絕；重試保留備註；`finish_ingest_job` 未命中不改狀態；例外保留 results；併發 approve 只有一個成功
- [x] 4.3 `src/tests/adminKnowledgeReports.test.tsx`：切換篩選送出 status 並重新分頁；重試沿用 `ingest_job.selected_urls`；分頁去重

## 5. 收尾

- [x] 5.1 `./init.sh` 全綠；LIFF `npx vitest run`／`tsc`／`eslint` 全綠
- [x] 5.2 勾選本 tasks
