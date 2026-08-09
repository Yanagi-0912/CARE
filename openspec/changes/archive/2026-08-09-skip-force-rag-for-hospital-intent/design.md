## Context

Log：第一個 `agent_decide` 即 `call=['get_rag_answer'] force_rag=True`，尚未有 location ToolMessage。

## Decisions

1. `_is_nearby_facility_intent(text) -> bool`  
   - True 若含院所搜尋線索：`醫院|診所|藥局|看醫生|就醫|急診|附近院所|找醫院|找診所|看診`（可含少數英／其他語關鍵字如 hospital/clinic）  
   - False 若同時含特定院所查詢線索：`在哪|地址|電話|怎麼去`（避免搶走 lookup）

2. force 邏輯：
   - 若 `nearby_facility_intent` 且無 tool_calls 且尚未用過 location 工具 → 注入 `request_location_quick_reply`（`force_location=True` log）  
   - 原 force RAG 條件再加：`and not _is_nearby_facility_intent(user_text)`

3. 不改 guardrail `allow_rag`（可仍為 True）；由 force 層分流。

## Non-Goals

- 完整 NLU／多語完整關鍵字表（先覆蓋 prod 中文案例與常見詞）
- 改 prompt 規則文案（可選小修但不阻塞）
