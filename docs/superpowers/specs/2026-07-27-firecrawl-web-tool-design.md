# Firecrawl Web Search → Agent Tool

## Goal

將 Firecrawl 從 `RagAnswerService` 內部 fallback 拆成獨立 Agent Tool `search_public_web`，由 Agent 決定何時上網查。

## Decisions

| 項目 | 選擇 |
|------|------|
| 消費者 | 僅 Agent tool calling |
| Tool 回傳 | 完整回答 +「以下參考網路公開資料」+ 來源 |
| 閘門 | 與 RAG 相同：`allow_rag=True` 才綁定 |
| RAG | 移除內建 web fallback；無 KB 命中回 `NO_HITS_MESSAGE` |

## Architecture

```
Agent bind_tools
├── get_rag_answer       → RagAnswerService（KB only）
├── search_public_web    → WebSearchService（Firecrawl + Gemini）
├── find_nearby_hospitals
└── request_location_quick_reply
```

## Components

- `WebSearchService`：search → whitelist → scrape → Gemini 回答 → 來源格式
- `app/tools/web_tools.py`：`search_public_web` + DI configure
- `registry.get_all_tools(include_rag_tool=..., include_web_tool=...)`；兩者皆跟 `allow_rag`

## Out of scope

- Handler 直接呼叫 Firecrawl
- 獨立 `allow_web` guardrail
- 強制 RAG 失敗後硬編碼呼叫 web
