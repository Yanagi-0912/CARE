## ADDED Requirements

### Requirement: 媒體抽出全文不強制 RAG

當最新使用者訊息為 `LineMediaHandler` 媒體抽出格式（以「以下為使用者傳送的{image|video|audio|file}媒體內容：」開頭）時，即使 `allow_rag` 為 True 且模型本步未產生 tool_calls，系統 SHALL NOT 強制注入 `get_rag_answer`。

系統 SHALL 讓模型依抽出內容直接回答或摘要；SHALL NOT 將整份媒體全文當作知識庫查詢字串強制檢索。

一般非媒體衛教文字在 `allow_rag=True` 且無 tool_calls 時，既有 force RAG 行為 SHALL 維持不變。

#### Scenario: 飲食指南 PDF 媒體全文不 force RAG

- **WHEN** 使用者訊息為「以下為使用者傳送的file媒體內容：」加上飲食指南抽出全文，`allow_rag=True`，且模型未產生 tool_calls
- **THEN** 系統不注入 `get_rag_answer`、不標記 `force_rag=True`，也不注入 `request_location_quick_reply`

#### Scenario: 一般衛教文字仍可 force RAG

- **WHEN** 使用者訊息為非媒體前綴的健康症狀描述，`allow_rag=True`，模型未產生 tool_calls
- **THEN** 系統仍可強制注入 `get_rag_answer`
