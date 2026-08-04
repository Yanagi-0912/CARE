## Why

CARE 目前定位為健康醫療助手，但 Agent 對症狀／衛教常不呼叫 `get_rag_answer`，且尚未覆蓋「醫療場景詐騙」（假藥、假醫師、假醫院簡訊、保證療效保健品等）。課程方向要求往醫療打詐發展；既有 RAG＋白名單 `.gov.tw` 管線可直接承載官方防詐／假藥資訊，缺的是 prompt、guardrail 與知識種子。

## What Changes

- 擴充 `SYSTEM_PROMPT`：角色加入「醫療識詐」；健康衛教與疑似醫療詐騙訊息在已提供 `get_rag_answer` 時必須先查庫再答；急匯款／點不明連結時勸阻並引導官方管道（如 165、衛福部／食藥署相關說明）。
- 擴充 Guardrail 分類提示：醫療詐騙相關訊息亦視為應啟用 RAG（`allow_rag=True`）。
- 更新 `get_rag_answer` 工具說明：涵蓋醫療詐騙／假藥／可疑醫療訊息查證。
- 新增醫療打詐知識種子清單（官方 URL 列表），供既有 `scripts/ingest_url.py` 手動／作業流程 ingest；本 change **不**強制執行線上 ingest。
- 更新 OpenSpec：`agent-architecture`（與必要時 `line-reply-rules`）要求上述行為。

## Capabilities

### New Capabilities

- （無；本 change 擴充既有 Agent／Guardrail／RAG 使用方式，不新增獨立 API capability。）

### Modified Capabilities

- `agent-architecture`：Guardrail 相關範圍含醫療詐騙；Agent prompt／工具使用規則要求健康與醫療識詐問題在工具可用時必須呼叫 `get_rag_answer`。
- `line-reply-rules`：若現有規則未涵蓋醫療識詐語氣／官方管道提醒，補上對應行為要求（仍維持純文字、繁中、RAG 前綴與來源保留）。

## Impact

- **程式**：`app/services/agent/prompt.py`、`app/services/guardrail/service.py`、`app/tools/rag_tools.py`；可選 `resources/` 或 `docs/` 種子 URL 檔。
- **API／route**：無新增或變更 HTTP API。
- **知識庫**：需營運 ingest 官方假藥／醫療詐騙頁；白名單已含 `gov.tw`，原則上不需改 whitelist（除非種子 URL 落在白名單外）。
- **測試**：單元測試覆蓋 prompt 關鍵規則字串、guardrail 分類 prompt 含醫療詐騙關鍵、工具 docstring；既有 agent／RAG 測試應持續通過。
- **測試計畫**：`python -m pytest tests/unit -q`（至少新增／更新與 prompt、guardrail、rag_tools 相關的單元測試）。
