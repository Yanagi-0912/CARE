## MODIFIED Requirements

### Requirement: 代理可用工具集

系統 SHALL 透過 `app/tools/registry.py` 的 `get_all_tools(include_rag_tool)` 組裝工具集。工具集 SHALL 固定包含 `find_nearby_hospitals`、`find_nearby_facilities_by_department`、`lookup_medical_facility`、`request_location_quick_reply`、`submit_knowledge_report` 與 `open_official_site`；`get_rag_answer` 與 `answer_from_uploaded_document` SHALL 可依 `include_rag_tool` 參數納入。工具集 SHALL NOT 包含 `search_public_web`。工具實例 SHALL 由 `app/dependencies.py`（composition root）透過 `configure_rag_tool` / `configure_medical_tools` 注入其依賴服務；`WebSearchService` SHALL 注入 `RagAnswerService`（非 agent tool）。

工具集 SHALL 固定包含 `suggest_department_for_symptom`，且不隨 `include_rag_tool` 開關（本能力不設功能旗標，見 symptom-department-guidance design 決策 10）。該工具與 `get_rag_answer` 的分流 SHALL 由代理依問句形態自行選擇，系統 SHALL NOT 另設獨立的意圖分類步驟。

#### Scenario: 納入 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=True)`
- **THEN** 回傳的工具集包含 `get_rag_answer`、`answer_from_uploaded_document`、`find_nearby_hospitals`、`find_nearby_facilities_by_department`、`lookup_medical_facility`、`request_location_quick_reply`、`submit_knowledge_report`、`open_official_site`，且不含 `search_public_web`

#### Scenario: 排除 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=False)`
- **THEN** 回傳的工具集仍包含 `submit_knowledge_report`、`open_official_site` 與醫療／位置相關工具，且不含 `get_rag_answer`、`answer_from_uploaded_document` 與 `search_public_web`

#### Scenario: 症狀科別建議工具常駐

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=True)` 或 `get_all_tools(include_rag_tool=False)`
- **THEN** 兩者回傳的工具集都包含 `suggest_department_for_symptom`

### Requirement: 醫療識詐與健康查詢必須優先使用 RAG

當本輪工具集已包含 `get_rag_answer`，且使用者問題屬於健康衛教（症狀、疾病、用藥、保健等）或醫療場景識詐查證時，代理 SHALL 先呼叫 `get_rag_answer` 再依工具結果回答，SHALL NOT 僅依模型自身知識逕行給出衛教建議或識詐結論。純寒暄或與健康／醫療識詐無關的短句可不呼叫該工具。系統提示（`SYSTEM_PROMPT`）SHALL 載明上述規則，並說明代理可協助辨識可疑醫療訊息，但不是執法人員、不代替報案；遇急著匯款或點不明連結時 SHALL 強烈勸阻並提示可向官方管道（例如 165 反詐騙諮詢專線）查證。

例外：使用者的訊息同時包含症狀描述**與掛號科別意圖**（例如「要掛哪一科」「該看什麼科」）且工具集含 `suggest_department_for_symptom` 時，代理 SHALL 改呼叫該工具。**僅有症狀描述而無掛號意圖者不屬於此例外**，仍 SHALL 走 `get_rag_answer`。`SYSTEM_PROMPT` SHALL 載明此分界與其反例。

#### Scenario: 症狀問題先查 RAG

- **WHEN** `allow_rag` 為 `True` 且使用者詢問症狀或衛教建議
- **THEN** 代理 SHALL 先呼叫 `get_rag_answer`

#### Scenario: 症狀加掛號意圖改走建議工具

- **WHEN** 使用者傳送「我肚子好痛要掛哪一科」且工具集含 `suggest_department_for_symptom`
- **THEN** 代理 SHALL 呼叫 `suggest_department_for_symptom`，SHALL NOT 改走 `get_rag_answer`

#### Scenario: 純症狀敘述不受影響

- **WHEN** 使用者傳送「我肚子好痛」而未問科別
- **THEN** 代理 SHALL 呼叫 `get_rag_answer`，行為與本 change 之前完全相同
