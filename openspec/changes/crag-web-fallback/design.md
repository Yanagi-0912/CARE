## Context

既有 `WebSearchService` + `FirecrawlClient` + `.gov.tw` whitelist 已由 `search_public_web` tool 使用。Light CRAG 在 KB 不足時回 `NO_HITS_MESSAGE`，Agent 須再 tool call 才上網，體驗斷裂且與完整 CRAG 的 WebFallback 不一致。

## Goals / Non-Goals

**Goals:**
- 單一工具 `get_rag_answer`：KB → CRAG →（必要時）WebSearchService
- 觸發：空檢索、grade `incorrect`、ambiguous 一次 rewrite 後仍不足
- 沿用現有 Firecrawl／白名單／`WEB_ANSWER_PREFIX` 來源格式
- 從 Agent registry 移除 `search_public_web`

**Non-Goals:**
- 不重寫 Firecrawl client／whitelist
- 不做 HyDE／multi-query／ingest pipeline
- 不改 medical tools

## Decisions

1. **注入 WebSearchService 到 RagAnswerService**  
   `web_search: WebSearchService | None = None`；`None` 或 flag off → 維持舊 `NO_HITS_MESSAGE`。

2. **觸發條件（選項 A）**  
   - retrieve 後無 docs → web  
   - CRAG incorrect → web（不 rewrite）  
   - ambiguous → rewrite once；仍 empty／incorrect／ambiguous → web  
   - correct → 既有 generate＋citations  

3. **CRAG 關閉時**  
   空檢索仍可 web fallback（與 A 一致）；有 docs 則直接 generate（現況）。

4. **移除 tool**  
   `get_all_tools` 不再 append `search_public_web`；`configure_web_tool` 可改為 no-op 或僅給非 agent 路徑；`get_rag_answer` 說明改為內含必要時公開網路。

5. **Feature flag**  
   `RAG_WEB_FALLBACK_ENABLED` default `True`；與 `FIRECRAWL` 缺失時 `WebSearchService` 回 `NO_ANSWER_MESSAGE`。

## Risks / Trade-offs

- [Agent 少一層「是否上網」判斷] → Mitigate：whitelist + 既有 web prompt「勿捏造」
- [延遲上升] → 僅在不足時觸發；可接受
- [測試／docs 仍提 search_public_web] → tasks 一併清

## Migration Plan

1. 接線 + 測試  
2. 部署後觀察 latency／web 呼叫量  
3. 可設 `RAG_WEB_FALLBACK_ENABLED=false` 緊急關閉
