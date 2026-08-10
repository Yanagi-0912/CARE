## ADDED Requirements

### Requirement: allow_rag 時未呼叫工具則強制 RAG

當 `allow_rag` 為 `True` 且本輪工具集包含 `get_rag_answer` 時，若 `agent` 節點的模型輸出未包含任何 tool call，且本輪對話訊息中尚未出現名稱為 `get_rag_answer` 的工具結果（`ToolMessage`），系統 SHALL 在進入 `tools_condition` 之前，將該輪 AI 回應改為呼叫一次 `get_rag_answer`，其查詢參數 SHALL 為最新一則使用者訊息內容——**除本 capability 其他要求另有例外規定者外**。若本輪已執行過 `get_rag_answer`，或 `allow_rag` 為 `False`，或工具集不含 `get_rag_answer`，系統 SHALL NOT 強制注入。

已規定的例外散見於本 capability 的其他要求（已走位置／院所工具、找院所意圖、媒體抽出全文、官網意圖）。除此之外還有兩個例外，於此一併規定：

- **指名院所查詢**：使用者訊息同時命中「指名查詢」與「院所詞彙」（例如「台大醫院在哪」）時，系統 SHALL NOT 強制注入 `get_rag_answer`
- **上傳文件問答**：使用者訊息判定為與其先前上傳文件相關的問題時，系統 SHALL 改為強制注入 `answer_from_uploaded_document`（若本輪工具集含之且尚未執行過），而 SHALL NOT 強制注入 `get_rag_answer`

#### Scenario: 指名院所查詢不強制 RAG

- **WHEN** `allow_rag` 為 `True`、模型無 tool call，且使用者訊息為指名院所的位置查詢（如「台大醫院在哪」）
- **THEN** 系統不注入 `get_rag_answer`

#### Scenario: 上傳文件問題改強制文件工具

- **WHEN** `allow_rag` 為 `True`、模型無 tool call，且使用者訊息判定為上傳文件相關問題，工具集含 `answer_from_uploaded_document` 且本輪尚未執行過
- **THEN** 系統注入 `answer_from_uploaded_document`，而非 `get_rag_answer`

#### Scenario: 健康問題模型未呼叫工具時強制查庫

- **WHEN** `allow_rag` 為 `True`，工具集含 `get_rag_answer`，模型回傳純文字且無 tool call，且本輪尚未有 `get_rag_answer` 工具結果
- **THEN** 系統注入 `get_rag_answer` tool call，流程進入 `tools` 節點執行該工具

#### Scenario: 已查過 RAG 不再強制

- **WHEN** 本輪訊息中已有 `get_rag_answer` 的工具結果，且後續 `agent` 節點產出無 tool call 的最終文字
- **THEN** 系統不注入額外 tool call，流程可進入 `END`

#### Scenario: 未開 RAG 不強制

- **WHEN** `allow_rag` 為 `False`
- **THEN** 即使模型無 tool call，系統也不注入 `get_rag_answer`
