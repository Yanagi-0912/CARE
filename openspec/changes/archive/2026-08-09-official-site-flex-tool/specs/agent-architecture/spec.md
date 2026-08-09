## MODIFIED Requirements

### Requirement: 代理可用工具集

系統 SHALL 透過 `get_all_tools(include_rag_tool)` 組裝工具集。工具集 SHALL 固定包含 `find_nearby_hospitals`、`lookup_medical_facility`、`request_location_quick_reply`、`submit_knowledge_report` 與 `open_official_site`；`get_rag_answer` SHALL 可依 `include_rag_tool` 納入。工具集 SHALL NOT 包含 `search_public_web`。工具實例 SHALL 由 `app/dependencies.py`（composition root）注入依賴。

#### Scenario: 納入 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=True)`
- **THEN** 回傳集合含 `get_rag_answer`、`submit_knowledge_report` 與 `open_official_site`，且不含 `search_public_web`

#### Scenario: 排除 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=False)`
- **THEN** 仍含 `submit_knowledge_report`、`open_official_site` 與醫療／位置工具，不含 `get_rag_answer`

## ADDED Requirements

### Requirement: 官網意圖時強制入口工具

當 guardrail／agent 路徑判定為官網／LIFF 入口意圖時，系統 SHALL 優先確保呼叫 `open_official_site`，且 SHALL NOT 因 `allow_rag` 強制注入 `get_rag_answer`。

#### Scenario: 官網意圖不 force RAG

- **WHEN** 使用者訊息命中官網入口意圖
- **THEN** 不注入 `get_rag_answer`；必要時注入 `open_official_site`
