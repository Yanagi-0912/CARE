## Context

Log：`has_sources=False` 但使用者看到 5 筆失效參考連結。工具未附來源時 Agent 仍被規則 8 要求「保留來源」而捏造。

## Goals / Non-Goals

**Goals:** 無真實來源 → 不輸出來源區塊；有真實來源 → 維持現況。  
**Non-Goals:** 修 KB 缺 url、改 CITE_TOP_K、改檢索。

## Decisions

1. **Prompt 規則 8 改寫**  
   - 工具輸出含 sources heading → 必須完整保留，不得改網址。  
   - 工具輸出**不含** sources heading → **嚴禁**自行新增來源標題、網址清單或假編號來源；正文可保留 `[1]` 這類文內引用字樣但不得附假 URL 清單。（更乾淨：也要求不要編造 URL；文內編號可保留因來自 context。）

2. **硬保險後置處理**（不依賴模型遵守）  
   在 `Agent.invoke` 組最終 `response` 時：  
   - 找到本輪 `get_rag_answer` ToolMessage  
   - 若 `not text_contains_sources_heading(tool_content)` 且 `text_contains_sources_heading(response)`  
     → 用 `split_at_sources_heading(response)` 只保留 heading 前正文  
   - 若 tool **有** heading 且 response 沒有 → 維持現有後補邏輯  
   - 可選 log：`stripped_fabricated_sources=True`

3. **輔助函式**  
   `strip_sources_section(text) -> str` 放 `i18n/messages.py` 或 agent 小 util，便於測。

4. **測試**  
   - mock graph／或測純函式 + agent 後置片段：無來源 tool → 最終無 heading  
   - 有來源 tool → 後補仍生效
