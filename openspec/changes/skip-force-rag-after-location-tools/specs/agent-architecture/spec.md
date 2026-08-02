## ADDED Requirements

### Requirement: 已走位置／院所工具時不強制 RAG

當系統在 `allow_rag=True` 下準備強制注入 `get_rag_answer` 時，若本輪對話訊息中已存在 `request_location_quick_reply`、`find_nearby_hospitals` 或 `lookup_medical_facility` 的工具結果，系統 SHALL NOT 強制注入 `get_rag_answer`。

#### Scenario: 請分享位置後不再 force RAG

- **WHEN** `allow_rag=True`、模型本步未產生 tool_calls，且訊息中已有 `request_location_quick_reply` 的 ToolMessage
- **THEN** 系統不注入 `get_rag_answer`，亦不標記 `force_rag=True`

#### Scenario: 健康問句仍可 force

- **WHEN** `allow_rag=True`、模型未產生 tool_calls，且尚未執行 RAG 或位置／院所工具
- **THEN** 系統仍可強制注入 `get_rag_answer`
