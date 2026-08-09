## Context

`KnowledgeReportService.approve` 現況（`app/services/knowledge_reports/service.py:92`）是一條龍：查報告 → 檢查狀態 → 正規化／白名單驗證 URL → 落庫 `reviewing` → **同步 for-loop ingest** → 依結果落庫 `resolved`／留 `reviewing`。router 直接 await 整段。

專案內既有的非同步慣例是長駐 scheduler（`app/services/consultation/scheduler.py:31`、`app/services/medication/medication_scheduler.py:76` 用 `asyncio.create_task` 跑 loop），沒有 Celery／arq 等外部 queue，也沒有 worker process。

## Goals / Non-Goals

**Goals:**
- approve 的 HTTP 回應時間不再受 ingest 來源站台影響
- 可從 `ingest_job` 分辨進行中／成功／失敗，失敗有可讀原因
- 失敗可重試，且不會重複啟動同一份 job

**Non-Goals:**
- 引入外部 job queue／worker（Celery、arq、n8n）或持久化重試排程
- 跨 process／多副本的分散式鎖
- ingest 進度百分比、逐 URL 即時推播（前端以刷新輪詢即可）

## Decisions

1. **背景執行用 FastAPI `BackgroundTasks`，不用 `asyncio.create_task`。**
   BackgroundTasks 綁 request 生命週期、由 Starlette 在回應送出後執行，例外會進 app 的錯誤處理而非變成無人接管的 task exception。且 `TestClient` 會同步執行 background task，測試能在一次呼叫內斷言最終狀態，不必 sleep。

2. **service 拆成兩個方法**，router 負責串接：
   - `approve(...)` → 只做驗證與「開工」落庫，回傳 `reviewing`/`running` 的 report
   - `run_ingest(report_id)` → 背景執行，逐 URL ingest 並寫最終狀態
   router：`background_tasks.add_task(service.run_ingest, report.report_id)`。
   `run_ingest` 只吃 `report_id` 再重讀一次，避免把 request-scope 的物件帶進背景。

3. **重試不新增端點**，沿用 approve。狀態機判斷：
   | 目前狀態 | approve 行為 |
   |---|---|
   | `pending` | 開工 |
   | `reviewing` + `ingest_job.status` 為 `failed`／`None` | 重試，覆寫 job |
   | `reviewing` + `running` 且 `started_at` 在逾時內 | **409** `Ingest already running` |
   | `reviewing` + `running` 且 `started_at` 已逾時 | 視為孤兒，允許重跑 |
   | `resolved`／`rejected` | 409（維持現況） |

4. **逾時門檻常數 `INGEST_JOB_STALE_AFTER = timedelta(minutes=10)`** 放 service 模組層。不做設定檔化——單一 process、值只影響「多久後允許人工重試」，可調性不值得多一個環境變數。

5. **`run_ingest` 全程包 try/except**：任何未預期例外都要把 job 收尾成 `failed` 並寫入 `error`，否則 job 會永遠卡在 `running` 直到逾時。

6. **`IngestJob.status` 用 `Optional[Literal[...]] = None`**，`None` 專門代表本 change 之前寫入的舊文件。判斷「是否進行中」一律問 `status == "running"`，legacy 自然不擋重試。

7. **`report.status` 與 `ingest_job.status` 分工**：`report.status` 是審核結論（`reviewing`→`resolved`），`ingest_job.status` 是這次 ingest 的執行結果。ingest 失敗時 report 留在 `reviewing`（維持現況語意：還沒結案、待處理）。

## Risks / Trade-offs

- **[process 重啟會遺失進行中的 job]** → 這是不引入外部 queue 的直接代價。以 `started_at` + 逾時讓 admin 可手動重跑，孤兒不會永久鎖死。若日後跑多副本，此設計需換成真 queue。
- **[approve 不再回終局狀態]** → 既有測試與任何直接讀 approve 回應判斷成敗的 client 會失準。前端目前不看回傳內容（只 refetch），影響僅止於測試。
- **[背景任務失敗使用者無感知]** → 由後續 `admin-review-queue-hardening` 在 UI 顯示 `ingest_job.error` 補齊；本 change 先保證資料寫得進去。
- **[同一 report 併發 approve]** → 409 是 read-then-write 的檢查，理論上有 race window。單一 admin 操作情境下可接受，不加 Mongo 條件更新。

## Migration Plan

無 DB migration。新欄位皆 optional，舊文件照讀。部署後第一次 approve 就會補上新欄位。

## Open Questions

- （無）
