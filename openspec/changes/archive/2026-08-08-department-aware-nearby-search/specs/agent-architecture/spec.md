## ADDED Requirements

### Requirement: 座標進入對話時依科別決定搜尋工具

系統 SHALL 於使用者訊息為座標文字（「這是我的目前位置：lat=…, lng=…」）且模型未主動呼叫工具時，
強制注入院所搜尋工具呼叫，以避免代理回傳空內容或將座標文字當成 RAG 查詢送出。

注入哪一個工具 SHALL 依對話歷史中是否存在科別需求決定：有科別則注入
`find_nearby_facilities_by_department`（並帶入使用者的原始說法），否則注入 `find_nearby_hospitals`。

#### Scenario: 歷史中有科別需求

- **WHEN** 使用者先傳「附近有腸胃科嗎」，本輪傳送座標，且模型未產生 tool_calls
- **THEN** 系統注入 `find_nearby_facilities_by_department`，`args` 含 `lat`、`lng` 與 `department="腸胃科"`

#### Scenario: 歷史中無科別需求

- **WHEN** 使用者先傳「附近有醫院嗎」，本輪傳送座標，且模型未產生 tool_calls
- **THEN** 系統注入 `find_nearby_hospitals`，`args` 僅含 `lat` 與 `lng`

## MODIFIED Requirements

### Requirement: 代理可用工具集

系統 SHALL 透過 `app/tools/registry.py` 的 `get_all_tools(include_rag_tool)` 組裝工具集。工具集 SHALL 固定包含 `find_nearby_hospitals`、`find_nearby_facilities_by_department`、`lookup_medical_facility`、`request_location_quick_reply`、`submit_knowledge_report` 與 `open_official_site`；`get_rag_answer` 與 `answer_from_uploaded_document` SHALL 可依 `include_rag_tool` 參數納入。工具實例 SHALL 由 `app/dependencies.py`（composition root）透過 `configure_rag_tool` / `configure_medical_tools` 注入其依賴服務。

#### Scenario: 納入 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=True)`
- **THEN** 回傳的工具集包含 `get_rag_answer`、`answer_from_uploaded_document`、`find_nearby_hospitals`、`find_nearby_facilities_by_department`、`lookup_medical_facility`、`request_location_quick_reply`

#### Scenario: 排除 RAG 工具

- **WHEN** 呼叫 `get_all_tools(include_rag_tool=False)`
- **THEN** 回傳的工具集不含 `get_rag_answer` 與 `answer_from_uploaded_document`，但仍包含 `find_nearby_hospitals`、`find_nearby_facilities_by_department`、`lookup_medical_facility`、`request_location_quick_reply`

### Requirement: 最終回覆組裝

系統 SHALL 從流程結果的最後一則 AI 訊息取得文字作為回覆，並偵測本輪是否呼叫了 `request_location_quick_reply`，於回傳結果中提供 `call_request_location` 旗標。醫療與入口類工具（`find_nearby_hospitals`、`find_nearby_facilities_by_department`、`lookup_medical_facility`、`request_location_quick_reply`、`open_official_site`）的輸出 SHALL 直接作為送往 LINE 的內容，不得由模型改寫，以免 Flex Message JSON 被破壞。當本輪呼叫過 `get_rag_answer` 且工具輸出含「參考資料來源：」但最終回覆遺漏時，系統 SHALL 以防禦性後置處理自動補回參考資料來源段落。

#### Scenario: 回覆遺漏參考來源時自動補回

- **WHEN** 本輪呼叫過 `get_rag_answer`，其輸出含「參考資料來源：」，但代理最終回覆未包含該段
- **THEN** 系統將工具輸出中的「參考資料來源：」段落附加到最終回覆末端

#### Scenario: 觸發位置快速回覆旗標

- **WHEN** 本輪呼叫過 `request_location_quick_reply`
- **THEN** 回傳結果的 `call_request_location` 為 `True`

#### Scenario: 科別搜尋回傳 Flex Message

- **WHEN** 本輪呼叫過 `find_nearby_facilities_by_department` 且其輸出為 Flex Message JSON
- **THEN** 系統以該工具輸出作為最終回覆，不經模型改寫
