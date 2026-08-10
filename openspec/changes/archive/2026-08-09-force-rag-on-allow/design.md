## Context

LangGraph：`guardrail → agent → (tools_condition) → tools|END`。`agent_node` 以 `bind_tools` 讓模型決定是否呼叫工具。實務上模型常在 `allow_rag=True` 時仍直接給文字回覆。

## Goals / Non-Goals

**Goals:**

- `allow_rag=True` 且模型未呼叫任何工具時，強制一次 `get_rag_answer`。
- 同一輪已執行過 `get_rag_answer`（訊息中已有對應 ToolMessage）後，不再強制，避免迴圈。
- `allow_rag=False` 或工具集不含 RAG 時不介入。

**Non-Goals:**

- 不改 guardrail 分類邏輯、不改 RAG／CRAG／web fallback。
- 不強制其他工具（院所、位置）。
- 不加新 HTTP API。

## Decisions

1. **在 `agent_node` 注入 tool_calls，而非改 graph 拓樸**  
   - 合成 `AIMessage` 帶 `tool_calls=[{name: get_rag_answer, args: {query: user_text}}]`，讓既有 `tools_condition` → `ToolNode` 接手。  
   - 替代：新節點 `force_rag` → 多一層邊與狀態；收益不大。

2. **以「本輪是否已有 get_rag_answer 的 ToolMessage」防迴圈**  
   - 查 `state["messages"]` 中 `ToolMessage.name == "get_rag_answer"`。  
   - 不新增 State 欄位，減少圖狀態變更。

3. **query 取最新 HumanMessage 文字**  
   - 與使用者本輪問題一致。

4. **log**  
   - `agent_decide` 既有 `call=` 會顯示強制後的名稱；另加 `force_rag=True` 方便過濾。

## Risks / Trade-offs

- [Risk] 寒暄被誤判 allow_rag → 多一次無意義查庫 → 由既有 guardrail 負責；本 change 不放寬分類。  
- [Risk] 合成 tool_call 的 id／格式需符合 LangChain／ToolNode 預期 → 用與模型相同的 tool_calls dict 結構並以單元測試驗證。  
- [Trade-off] 強制查庫後若 KB／web 仍空，使用者仍可能收到 RAG_ERR；這是資料問題，非本 change 範圍。

## Migration Plan

部署後即生效；無需 DB 遷移。Rollback：還原 `nodes.py`。

## Open Questions

- （無）
