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
- `rag-crag`：**無 delta**。原先預期要撤回 `light-crag` 引入的「不在 RAG 服務內觸發網路搜尋」，但實際查核後該條從未寫進主規格——`openspec/specs/rag-crag/spec.md` 現有三條（檢索充足性分級／模糊時最多一次改寫重試／Grader 失敗時降級）皆與網路無關。因此本 change 不附 `specs/rag-crag/spec.md`；真的補上 REMOVED delta 反而會因為「移除不存在的 requirement」讓 archive 中止。
- `agent-architecture`：工具集不再包含 `search_public_web`。

## 歸檔順序

`light-crag`（`archive/2026-08-09-light-crag`）與 `official-site-flex-tool`（`archive/2026-08-09-official-site-flex-tool`）皆已歸檔，本 change 是最後一個動到「代理可用工具集」的 change。

原訂「`official-site-flex-tool` MUST 在本 change 之後」的硬性順序已來不及遵守，且那次歸檔確實讓 `open_official_site` 從主規格的工具清單中消失（`get_all_tools` 實際有、spec 沒有）。因應方式是不再依賴歸檔順序：本 change 的 `specs/agent-architecture/spec.md` MODIFIED 區塊直接列出與 `registry.get_all_tools` 完全一致的工具清單（MODIFIED 是整塊取代），歸檔後主規格即回到正確狀態。主規格的漏列已於本 change 一併補回，兩邊內容相同，archive 為冪等。

## Impact

- **程式**：`answer_service.py`、`dependencies.py`、`registry.py`、`rag_tools.py`；可保留 `web_tools.py` 供測試或標記 deprecated
- **行為**：使用者只經 `get_rag_answer` 即可拿到網路補充答案（仍限白名單）
- **測試**：`test_answer_service`、`test_registry`、相關 agent／tool 測試
- **設定**：沿用 `FIRECRAWL_API_KEY`；可選 `RAG_WEB_FALLBACK_ENABLED`（default true）便於關閉
