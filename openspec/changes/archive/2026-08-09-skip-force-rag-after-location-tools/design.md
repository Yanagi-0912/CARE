## Context

Log rid=1881388d：`request_location_quick_reply` → 下一輪 `force_rag=True` + `get_rag_answer`。

## Decisions

1. Helper `_already_used_location_tools(messages) -> bool`：任一 ToolMessage.name 屬於  
   `request_location_quick_reply` | `find_nearby_hospitals` | `lookup_medical_facility`。

2. force 條件改為：
   ```
   allow_rag and rag_in_tools and not tool_calls
   and not _already_ran_rag(...)
   and not _already_used_location_tools(...)
   ```

3. 不改 guardrail（「我要看醫院」仍可能 allow_rag=True）；硬擋在 force 層即可。

## Non-Goals

- 改 guardrail 分類器
- 拿掉 force RAG 整體機制
