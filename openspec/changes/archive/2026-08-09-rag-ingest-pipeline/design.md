## Context

Retriever 投影 `text`／`embedding`／`source_name`／`url`。Web 抓取已有 `FirecrawlClient.scrape`＋`is_allowed_url`。缺的是寫側編排。

## Goals / Non-Goals

**Goals:**
- 單一 URL 入庫可程式化、可 CLI
- 僅白名單網域
- embedding 模型／維度與 runtime query 一致
- 同 URL 可重跑（replace）

**Non-Goals:**
- PDF／spatial parse
- 知識回報／審核佇列
- 批次爬全站
- 改答題管線

## Decisions

1. **模組**  
   - `chunking.py`：純函式 `split_text_to_chunks(text) -> list[str]`  
   - `ingest_service.py`：`IngestService.ingest_url(url, *, source_name=None) -> IngestResult`  
   - Script 組裝 settings／Firecrawl／embeddings／Motor collection

2. **切塊（KISS）**  
   - 優先依 Markdown 標題／雙換行分段；超長段再按字元窗切（預設 ~1200，overlap ~100）  
   - 空段丟棄；至少保留可檢索段落

3. **文件契約**  
   ```
   {
     <MONGODB_TEXT_FIELD>: chunk_text,
     <MONGODB_VECTOR_FIELD>: embedding,
     source_name, url,
     content_hash,   # sha256(chunk_text) 便於除錯
     chunk_index, ingested_at
   }
   ```

4. **同 URL**  
   `delete_many({"url": url})` 後 `insert_many`（簡單可重播）

5. **失敗**  
   - 非白名單 → 明確錯誤／結果 `rejected`  
   - scrape 空 → 不寫庫  
   - embed 失敗 → 中止、不留下半套（先刪後若 embed 失敗需注意：應先 embed 全部再 delete+insert，或 transaction；採 **先算完全部 docs 再 replace**）

6. **CLI**  
   `python scripts/ingest_url.py --url ... [--source-name ...] [--dry-run]`

## Risks

- [切塊過粗／過細] → 先固定參數；之後再調  
- [Firecrawl markdown 品質] → 沿用現有 scrape；壞頁人工換來源  
- [誤刪 url] → CLI 需確認參數；dry-run 先印 chunk 數
