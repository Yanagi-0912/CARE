## ADDED Requirements

### Requirement: 找院所意圖不強制 RAG，改強制請位置

當最新使用者訊息屬於「尋找附近醫院／診所／藥局或要去看醫生」意圖，且模型本步未產生 tool_calls、且尚未執行過位置／院所工具時，系統 SHALL NOT 強制注入 `get_rag_answer`，SHALL 改強制注入 `request_location_quick_reply`。

若訊息同時含查詢特定院所位置的線索（例如「在哪」「地址」），系統 SHALL NOT 套用上述強制請位置（留給院所查詢工具路徑）。

#### Scenario: 「我要看醫院」強制請位置

- **WHEN** 使用者訊息為「我要看醫院」，`allow_rag` 可為 True，且模型未產生 tool_calls
- **THEN** 系統注入 `request_location_quick_reply`，且不注入 `get_rag_answer`／不標記 `force_rag=True`

#### Scenario: 健康症狀仍可 force RAG

- **WHEN** 使用者訊息為健康症狀描述且非找院所意圖，`allow_rag=True`，模型未產生 tool_calls
- **THEN** 系統仍可強制注入 `get_rag_answer`
