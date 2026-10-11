# CARE v3 Batch Implementation Plan

> **For agentic workers:** Use executing-plans for root integration, dispatch independent snapshot and transport tasks in parallel. Steps use checkbox tracking.

**Goal:** 完成可預檢、可安全續跑的 CARE v3 離線 Batch 實驗準備與最終整理。
**Architecture:** SQLite 收據與 Batch future 適配保留既有 RAG 決策；獨立快照工具保存固定資料庫。
**Tech Stack:** Python 3.12、google-genai 2.23、SQLite、asyncpg/Motor、pytest。
**Spec:** ../specs/2026-10-11-healthnews-batch-design.md

## Global Constraints

- 不修改線上 CARE 行為／prompt；不提交正式實驗。
- input 只含 question/language；每輪獨立；no web/cache/writeback。
- 不捏造 old 快照、人工／醫療審查結果或 API 成功。
- 測試依賴注入，不使用 monkeypatch；新功能先紅後綠。

## Review Focus

- 提交後程式中斷：無 job ID 時停在 unknown，不能默默重送付費請求。
- 相同題目跨組／跨輪：各自獨立 request identity。
- 投機取消：已提交仍保留 provider response、usage，不影響採用結果。
- 續跑：相同 run 的精排／grader／retrieval 收據固定，不取得新證據。
- 快照內容／索引漂移與設定修改：在新付費工作前拒絕。

## Tasks

- [x] 1. `scripts/healthnews_batch/store.py` + `transport.py`：持久 identity、receipts、jobs、requests、results；Batch adapter。先測未知提交、反序結果、取消及跨輪隔離，再實作。
- [x] 2. `scripts/healthnews_snapshot.py`：plan/create/check；拒絕 live/覆寫、內容漂移、ID 不一致；DI 測試後實作。root 負責外部建立與核對。
- [x] 3. `scripts/healthnews_batch/config.py`：固定設定、檔案 hash、input allowlist、preflight；tests/unit/scripts/test_healthnews_batch_config.py。
- [x] 4. `scripts/healthnews_batch/runtime.py` + `scripts/healthnews_batch_experiment.py`：復用真實 RAG、durable local receipts、Batch 驅動、output/reviews、CLI preflight/run/status/reconcile。完整 fake-provider 端到端與續跑先紅後綠。
- [x] 5. 固定題庫副本、正式 JSON 設定、快照計畫與操作文件；實際 preflight，保持不提交。
- [x] 6. 針對性測試、./init.sh、獨立最終 review、修正；保存驗證紀錄與最終整理。

## Ledger

Ruling: 使用獨立 worktree 承接已授權工作；不再索取例行分支／設計批准。使用者已核准上輪具體 Batch 流程。
Ruling: 人工審核依使用者確認記為 user_attested_complete；基準綁定目前已展示的 v3 hash，不改原始題庫 pending 欄位。
Ruling: 新庫快照若外部審批仍拒絕，完成其餘工具並清楚列為 BLOCKED；不將 plan 假報為已建立。

實體核對：新庫 Mongo/PG 各 18,861 筆，來源與複本 hash/ID 一致，PG 索引一致；Atlas code20 索引容量已滿。工具、設定與持久備份完成，新文字索引 READY 仍 BLOCKED。cloud preflight 正確拒絕提交；正式 jobs=0。

驗證：新增58項全部通過；./init.sh 全部非外部整合測試通過，9869 passed、6 skipped、15 deselected。測試使用假憑證；LINE/真實資料庫 integration 不列成功。獨立review無剩餘重要問題。
