## MODIFIED Requirements

### Requirement: 代理可用工具集

系統 SHALL 透過 `get_all_tools(include_rag_tool)` 組裝工具集。工具集 SHALL 固定包含 `find_nearby_hospitals`、`lookup_medical_facility`、`request_location_quick_reply` 與 `submit_knowledge_report`；`get_rag_answer` SHALL 可依 `include_rag_tool` 納入。工具集 SHALL NOT 包含 `search_public_web`。

#### Scenario: 納入 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=True)`
- **THEN** 回傳集合含 `get_rag_answer` 與 `submit_knowledge_report`，且不含 `search_public_web`

#### Scenario: 排除 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=False)`
- **THEN** 仍含 `submit_knowledge_report` 與醫療／位置工具，不含 `get_rag_answer`
