前置：`knowledge-report-async-ingest` 需先完成（第 3 節依賴其 `ingest_job.status`）。

## 1. 後端：status 驗證與分頁

- [x] 1.1 `list_knowledge_reports_for_admin` 的 `status` 改型別為 `Optional[KnowledgeReportStatus]`（非法值 → 422）
- [x] 1.2 同端點新增 `limit: int = Query(50, ge=1, le=200)`、`offset: int = Query(0, ge=0)`
- [x] 1.3 `KnowledgeReportListResponse` 新增 optional `total`／`limit`／`offset`
- [x] 1.4 repository：`list_by_statuses` 支援 `limit`／`offset`；新增 `count_by_statuses`
- [x] 1.5 service `list_for_admin` 回傳 (reports, total)
- [x] 1.6 `tests/unit/routers/test_knowledge_reports.py`：422（status／limit）、預設分頁、指定 offset
- [x] 1.7 `tests/unit/repositories/test_knowledge_report_repository.py`：limit／offset 傳遞與 count

## 2. 前端：型別與 API client

- [x] 2.1 `knowledgeReportsApi.ts` 新增 `IngestJobDto`／`IngestJobResultDto`，`KnowledgeReportDto` 加 `ingest_job?`
- [x] 2.2 `KnowledgeReportListResponse` 加 optional `total`／`limit`／`offset`
- [x] 2.3 `fetchAdminKnowledgeReports` 接受 `{ status?, limit?, offset? }`

## 3. 前端：ingest 狀態與重試

- [x] 3.1 卡片顯示 ingest 狀態標記（進行中／失敗）
- [x] 3.2 詳情 dialog 展開 job `error` 與逐 URL `status`／`chunk_count`／`message`
- [x] 3.3 失敗時主要動作改為「重試」，沿用 approve；409 錯誤顯示於 dialog

## 4. 前端：URL 勾選

- [x] 4.1 dialog 內 `user_source_urls` 改為 checkbox 清單，預設全選
- [x] 4.2 核准送出 `selected_urls`；全部取消勾選時停用核准鈕
- [x] 4.3 `closeDialog` 重置勾選狀態

## 5. 前端：分頁

- [x] 5.1 佇列以 `limit`／`offset` 載入，顯示總筆數
- [x] 5.2 「載入更多」append 下一頁；已載滿則隱藏
- [x] 5.3 核准／拒絕成功後重載第一頁

## 6. 測試與收尾

- [x] 6.1 `src/tests/adminKnowledgeReports.test.tsx`：部分勾選核准送出正確 `selected_urls`、全不選時停用、ingest 失敗顯示原因、重試呼叫 approve、載入更多
- [x] 6.2 `./init.sh` 全綠；LIFF `npx vitest run` 全綠
- [ ] 6.3 勾選本 tasks；commit
