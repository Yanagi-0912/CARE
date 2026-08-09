## 1. Model

- [x] 1.1 `IngestJob` 新增 `status: Optional[Literal["running","succeeded","failed"]] = None`、`started_at`、`finished_at`（皆 optional，向下相容）

## 2. Service

- [x] 2.1 `approve` 改為只做驗證＋開工落庫（`reviewing` / `ingest_job.status=running` / `started_at`），移除同步 ingest 迴圈
- [x] 2.2 新增 `run_ingest(report_id)`：重讀報告、逐 URL ingest、寫入 `succeeded`／`failed` 與 `finished_at`；全程 try/except 保證不停留在 `running`
- [x] 2.3 `approve` 併發保護：`running` 且 `started_at` 未逾時 → 409；逾時（`INGEST_JOB_STALE_AFTER`＝10 分鐘）或 `status` 為 `failed`／`None` → 允許重跑

## 3. Router

- [x] 3.1 `approve_knowledge_report` 注入 `BackgroundTasks`，成功後 `add_task(service.run_ingest, report_id)`

## 4. 測試

- [x] 4.1 `tests/unit/services/knowledge_reports/test_service.py`：既有 `test_approve_success`／`test_approve_ingest_failure_stays_reviewing` 拆成 approve（開工）與 `run_ingest`（結果）兩段斷言
- [x] 4.2 同檔新增：running 未逾時 → 409；running 已逾時 → 可重跑；`ingest_job.status=None` → 可重跑；`run_ingest` 遇例外 → 收斂為 `failed`
- [x] 4.3 `tests/unit/routers/test_knowledge_reports.py`：approve 回應為 `reviewing`＋`running`，且背景工作被排入（TestClient 會執行 background task）
- [x] 4.4 依 openspec rules 以 DI 傳入 mock，禁止 monkey patch

## 5. 收尾

- [x] 5.1 `./init.sh` 全綠
- [ ] 5.2 勾選本 tasks；commit
