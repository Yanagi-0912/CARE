## Why

CARE 只讀 Mongo 裡現成的 chunk，repo 內沒有「白名單網頁 → 切塊 → embed → 寫回」管線。知識品質上限卡在進庫前；營運也無法在人工確認 URL 後用同一套流程增量補庫。

## What Changes

- 新增 `IngestService`：白名單 URL → Firecrawl scrape → 結構友善切 chunk → 與查詢相同 embedding → upsert 既有向量 collection。
- 新增 `scripts/ingest_url.py`：人工審核通過後手動執行入庫（可 dry-run）。
- 同 URL 重跑：刪除該 url 舊 chunk 後再寫入（可重播、避免重複）。
- 單元測試覆蓋：非白名單拒絕、切塊、去重重寫、欄位契約。
- **不做**：PDF／LiteParse、知識回報 API／LIFF、Agent ingest tool、換 embedding 模型。

## Capabilities

### New Capabilities

- `rag-ingest`：白名單網頁增量入庫契約（服務＋CLI）。

### Modified Capabilities

- （無；讀側 `rag-responses` 不變。）

## Impact

- **程式**：`app/services/rag/ingest_*.py`（或單一 `ingest_service.py`＋`chunking.py`）、`scripts/ingest_url.py`、可選 DI helper
- **資料**：寫入既有 `MONGODB_COLLECTION`（欄位對齊 `MONGODB_TEXT_FIELD`／`MONGODB_VECTOR_FIELD`＋`source_name`／`url`）
- **營運**：需 `FIRECRAWL_API_KEY`、Mongo、Gemini embedding
- **測試**：mock scrape／embed／collection，不打真實外網
