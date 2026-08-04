## ADDED Requirements

### Requirement: allow_rag 時未呼叫工具則強制 RAG

當 `allow_rag` 為 `True` 且本輪工具集包含 `get_rag_answer` 時，若 `agent` 節點的模型輸出未包含任何 tool call，且本輪對話訊息中尚未出現名稱為 `get_rag_answer` 的工具結果（`ToolMessage`），系統 SHALL 在進入 `tools_condition` 之前，將該輪 AI 回應改為呼叫一次 `get_rag_answer`，其查詢參數 SHALL 為最新一則使用者訊息內容。若本輪已執行過 `get_rag_answer`，或 `allow_rag` 為 `False`，或工具集不含 `get_rag_answer`，系統 SHALL NOT 強制注入。

#### Scenario: 健康問題模型未呼叫工具時強制查庫

- **WHEN** `allow_rag` 為 `True`，工具集含 `get_rag_answer`，模型回傳純文字且無 tool call，且本輪尚未有 `get_rag_answer` 工具結果
- **THEN** 系統注入 `get_rag_answer` tool call，流程進入 `tools` 節點執行該工具

#### Scenario: 已查過 RAG 不再強制

- **WHEN** 本輪訊息中已有 `get_rag_answer` 的工具結果，且後續 `agent` 節點產出無 tool call 的最終文字
- **THEN** 系統不注入額外 tool call，流程可進入 `END`

#### Scenario: 未開 RAG 不強制

- **WHEN** `allow_rag` 為 `False`
- **THEN** 即使模型無 tool call，系統也不注入 `get_rag_answer`
