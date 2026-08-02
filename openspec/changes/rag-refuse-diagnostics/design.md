## Context

`RagAnswerService.answer`／`WebSearchService.answer` 在生成後用 `CANNOT_ANSWER_MARKERS` 子字串匹配決定 MODEL_REFUSE，但 fail log 僅 `rag_fail code=...`。

## Goals / Non-Goals

**Goals:** refuse 當下留下可應證證據（marker + preview）。  
**Non-Goals:** 改判定邏輯、修誤殺、改 fail 文案、加 web fallback。

## Decisions

1. **輔助函式** `matched_cannot_answer_marker(text) -> str | None`  
   - 空／空白 → 視為 `"<empty>"`（或回傳特殊值並在 log 用 `matched_marker="<empty>"`）  
   - 否則回傳第一個命中的 marker；未命中回傳 `None`（理論上 refuse 路徑不會）

2. **Log 格式**（與既有 `log_stage`／`logger.info` 風格一致即可）：
   ```
   logger.info(
       "rag_fail code=%s matched_marker=%s answer_preview=%s",
       RagFailCode.MODEL_REFUSE,
       matched,
       preview,
   )
   ```
   - `answer_preview`：正規化後取前 200 字元，空白壓成單空白；勿 log 整篇超長。

3. **套用點**：`answer_service` 與 `web_search_service` 兩處 MODEL_REFUSE 分支皆記錄。  
   KB 路徑可改 `_fail` 不通用（其他 code 無需 preview）；僅 MODEL_REFUSE 專用 log 即可。

4. **測試**：mock logger 或 caplog，餵含「無法」的答案，assert log 含 `matched_marker=無法` 與 preview 片段。

## Risks

- Preview 可能含敏感衛教內容 → 限制 200 字；與既有 tool_result preview 同級可接受。
