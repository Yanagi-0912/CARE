## Why

Light CRAG 在知識庫不足時只回「無資料」字串，靠 Agent 再決定是否呼叫 `search_public_web`，行為不穩定。要對齊完整 CRAG：不足／空結果時在同一條 `get_rag_answer` 管線內用既有 Firecrawl＋白名單自動 web fallback，並移除 Agent 的 web tool calling。

## What Changes

- `RagAnswerService`：空檢索、CRAG `incorrect`、ambiguous 改寫後仍不足時，呼叫注入的 `WebSearchService.answer`（既有 Firecrawl／whitelist 實作）。
- **BREAKING（agent tools）**：自 `get_all_tools` 移除 `search_public_web`；guardrail 閘門只剩 `get_rag_answer`（醫療相關時）。
- 更新 `get_rag_answer` docstring：不再提示另呼叫 web tool。
- 修改 OpenSpec／main 契約：廢除「RAG 不得自動打網」；改為不足時 SHALL fallback。
- 單元測試：空結果／incorrect／ambiguous 耗盡 → web；web 失敗回友善訊息；registry 不再含 `search_public_web`。

## Capabilities

### New Capabilities

- （無；擴充既有。）

### Modified Capabilities

- `rag-responses`：RAG 可在不足時內建 web fallback；移除獨立 web tool 閘門要求（或改為僅內部服務）。
- `rag-crag`（若仍以 delta 存在於 light-crag change：以 main／本 change delta 為準）：不足路徑改為呼叫 WebSearchService，而非禁止上網。
- `agent-architecture`：工具集不再包含 `search_public_web`（若該 spec 有列工具）。

## Impact

- **程式**：`answer_service.py`、`dependencies.py`、`registry.py`、`rag_tools.py`；可保留 `web_tools.py` 供測試或標記 deprecated
- **行為**：使用者只經 `get_rag_answer` 即可拿到網路補充答案（仍限白名單）
- **測試**：`test_answer_service`、`test_registry`、相關 agent／tool 測試
- **設定**：沿用 `FIRECRAWL_API_KEY`；可選 `RAG_WEB_FALLBACK_ENABLED`（default true）便於關閉
