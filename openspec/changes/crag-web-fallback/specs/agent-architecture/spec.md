## MODIFIED Requirements

### Requirement: 代理可用工具集

系統 SHALL 透過 `app/tools/registry.py` 的 `get_all_tools(include_rag_tool)` 組裝工具集。工具集 SHALL 固定包含 `find_nearby_hospitals`、`lookup_medical_facility` 與 `request_location_quick_reply`；`get_rag_answer` SHALL 可依 `include_rag_tool` 參數納入。工具集 SHALL NOT 包含 `search_public_web`。工具實例 SHALL 由 `app/dependencies.py`（composition root）透過 `configure_rag_tool` / `configure_medical_tools` 注入其依賴服務；`WebSearchService` SHALL 注入 `RagAnswerService`（非 agent tool）。

#### Scenario: 納入 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=True)`
- **THEN** 回傳的工具集包含 `get_rag_answer`、`find_nearby_hospitals`、`lookup_medical_facility`、`request_location_quick_reply`，且不含 `search_public_web`

#### Scenario: 排除 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=False)`
- **THEN** 回傳的工具集僅包含醫療／位置相關工具，且不含 `get_rag_answer` 與 `search_public_web`
