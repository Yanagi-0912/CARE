## Why

`POST /api/admin/knowledge-reports/{id}/approve` 目前在 request 內同步逐一 ingest 所有 URL（`service.approve` 迴圈 await `ingest_url`）。URL 多或來源站台慢時，admin 的 HTTP 請求會被卡住數十秒甚至逾時；即使 ingest 實際已完成，前端也可能已斷線而看不到結果。

同時 `ingest_job` 只是「事後紀錄」，沒有進行中狀態，無法分辨「正在跑」與「跑完但失敗」——兩者在列表上都只是停在 `reviewing`。

## What Changes

- `approve` 改為**立即回應**：驗證（存在、狀態、白名單）仍同步執行並沿用既有錯誤碼，通過後寫入 `status=reviewing` + `ingest_job.status=running` 就回傳，實際 ingest 交由 FastAPI `BackgroundTasks` 於回應後執行
- `IngestJob` 新增 `status`（`running`／`succeeded`／`failed`）、`started_at`、`finished_at`
- 重試沿用同一支 approve（不新增端點）；`ingest_job.status=running` 且未逾時時回 **409**，避免重複啟動
- `running` 超過逾時門檻（預設 10 分鐘，視為 process 重啟遺留的孤兒 job）允許重新 approve
- 對 API 使用者的可見變更：approve 回應的 `status` 一律是 `reviewing`、`ingest_job.status=running`，**不再**直接回 `resolved`

## Capabilities

### Modified Capabilities

- `knowledge-reports`：approve 的執行語意由同步 ingest 改為背景 ingest；`ingest_job` 增加生命週期狀態與重試規則

## Impact

- **CARE**：`app/models/knowledge_report.py`、`app/services/knowledge_reports/service.py`、`app/routers/admin/knowledge_reports.py`
- **測試**：`tests/unit/services/knowledge_reports/test_service.py`、`tests/unit/routers/test_knowledge_reports.py`（既有 `test_approve_success` 等對「approve 後即 resolved」的斷言需拆成 start／run 兩段）
- **CARE-LIFF**：本 change 不改前端；前端顯示與重試 UI 由後續 `admin-review-queue-hardening` 承接
- **資料相容**：舊文件的 `ingest_job` 無新欄位，`status` 以 `None` 視為 legacy 已結束，不阻擋重試
