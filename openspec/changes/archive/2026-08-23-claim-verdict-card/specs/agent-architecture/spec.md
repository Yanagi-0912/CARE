## MODIFIED Requirements

### Requirement: 代理可用工具集

系統 SHALL 透過 `app/tools/registry.py` 的 `get_all_tools(include_rag_tool)` 組裝工具集。工具集 SHALL 固定包含 `find_nearby_hospitals`、`find_nearby_facilities_by_department`、`lookup_medical_facility`、`request_location_quick_reply`、`submit_knowledge_report` 與 `open_official_site`；`get_rag_answer` 與 `answer_from_uploaded_document` SHALL 可依 `include_rag_tool` 參數納入。工具集 SHALL NOT 包含 `search_public_web`。工具實例 SHALL 由 `app/dependencies.py`（composition root）透過 `configure_rag_tool` / `configure_medical_tools` 注入其依賴服務；`WebSearchService` SHALL 注入 `RagAnswerService`（非 agent tool）。

`verify_claim` SHALL 於 `include_rag_tool` 為真、且查核服務已配置（`CLAIM_VERIFICATION_ENABLED`，判定見 `is_claim_tool_configured`）時一併納入——兩者皆為真才提供，SHALL NOT 為此另設第二個布林參數。`verify_claim` 與 `get_rag_answer` 同屬「guardrail 放行後才提供」的知識庫工具，代理 SHALL 依問句形態自行在兩者之間選擇；該選擇即為查核型與衛教型的分流，系統 SHALL NOT 另設獨立的意圖分類步驟。

#### Scenario: 納入 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=True)`
- **THEN** 回傳的工具集包含 `get_rag_answer`、`answer_from_uploaded_document`、`find_nearby_hospitals`、`find_nearby_facilities_by_department`、`lookup_medical_facility`、`request_location_quick_reply`、`submit_knowledge_report`、`open_official_site`，且不含 `search_public_web`

#### Scenario: 排除 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=False)`
- **THEN** 回傳的工具集仍包含 `submit_knowledge_report`、`open_official_site` 與醫療／位置相關工具，且不含 `get_rag_answer`、`answer_from_uploaded_document`、`verify_claim` 與 `search_public_web`

#### Scenario: 查核型問句

- **WHEN** 使用者問「網傳 X 是真的嗎」且 guardrail 放行
- **THEN** 工具集含 `verify_claim`，代理可選用之

#### Scenario: 衛教型問句不受影響

- **WHEN** 使用者問「糖尿病可以吃水果嗎」
- **THEN** 代理選用 `get_rag_answer`，行為與本 change 之前完全相同

#### Scenario: 功能關閉時回到原行為

- **WHEN** `CLAIM_VERIFICATION_ENABLED` 為 false
- **THEN** 工具集不含 `verify_claim`，其餘工具與本 change 之前完全相同
