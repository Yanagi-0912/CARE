# RAG Ingest Pipeline Design

**Date:** 2026-08-02  
**Status:** Approved (scope A: pipeline + CLI only)  
**OpenSpec:** `openspec/changes/rag-ingest-pipeline/`

## Summary

Add write-side ingest for whitelist web pages: Firecrawl scrape → chunk → embed (same model/dim as query) → replace-by-url into existing Mongo vector collection. Operators run `scripts/ingest_url.py` after human review. No PDF parser, no knowledge-report API in this change.

## Pipeline

```text
URL → is_allowed_url?
    → FirecrawlClient.scrape
    → split_text_to_chunks
    → aembed_documents (Gemini, MONGODB_VECTOR_DIM)
    → delete_many({url}) + insert_many(docs)   # only after all embeds succeed
```

## Document fields

- `MONGODB_TEXT_FIELD` (default `text`)
- `MONGODB_VECTOR_FIELD` (default `embedding`)
- `source_name`, `url`
- `content_hash`, `chunk_index`, `ingested_at`

## Out of scope

PDF/LiteParse, knowledge reports, agent tool, full-site crawl, embedding model change.
