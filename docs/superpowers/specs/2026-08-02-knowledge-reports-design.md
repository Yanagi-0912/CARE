# Knowledge Reports Design

**Date:** 2026-08-02  
**Status:** Approved for implementation  
**OpenSpec:** `openspec/changes/knowledge-reports/`

## Flow

```text
LINE tool / POST API → pending report
Admin PATCH approve + selected_urls → IngestService per URL → resolved
LIFF GET /api/knowledge-reports → real list
```

## Auth

- User: JWT Bearer (`get_current_user`)
- Admin: `X-Admin-Key` = `KNOWLEDGE_REPORTS_ADMIN_API_KEY`

## Reuse

Existing `IngestService.ingest_url` only; no PDF.
