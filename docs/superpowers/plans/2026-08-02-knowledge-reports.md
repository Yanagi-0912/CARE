# Knowledge Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** 知識回報狀態機＋核准自動 ingest＋LIFF 真列表。

**Architecture:** Mongo `knowledge_reports` → Service（approve 呼叫 `IngestService`）→ User JWT API＋Admin API key＋Agent tool（contextvars `line_user_id`）→ LIFF fetch。

**Tech Stack:** FastAPI、Motor、pytest、React/Vitest LIFF

## Global Constraints

- 重用 `IngestService`；不做 PDF
- Admin：`X-Admin-Key` / `KNOWLEDGE_REPORTS_ADMIN_API_KEY`
- Work in CARE `jamesbranch`；LIFF 改 sibling `CARE-LIFF`
- Commit CARE 到 jamesbranch when tasks done (user requested push)
- Minimal diffs

---

### Task 1: Backend domain + API + tool

Implement under `/Users/jamessu/Desktop/computersciencehomework/CARE`:

**Create:**
- `app/models/knowledge_report.py` — pydantic／dataclass fields
- `app/repositories/knowledge_report_repository.py`
- `app/services/knowledge_reports/service.py`
- `app/routers/users/knowledge_reports.py` — user routes
- `app/routers/admin/knowledge_reports.py` — approve/reject
- `app/tools/knowledge_report_tools.py` — tool + contextvar + configure
- tests under `tests/unit/...`

**Modify:**
- `app/db/mongodb.py` — `get_knowledge_reports_collection`
- `app/core/config.py` + `.env.example` — admin key
- `app/dependencies.py` — wire service, ingest client for approve, configure tool, getters
- `app/main.py` — include routers；ensure_indexes
- `app/tools/registry.py` — always append `submit_knowledge_report`
- `app/services/line_messaging/handler/message_handler.py` — set/reset line_user_id context around agent.invoke
- `tests/unit/tools/test_registry.py`

**API sketch:**
- `POST /api/knowledge-reports` body `{question, reason, user_note?, user_source_urls?}`
- `GET /api/knowledge-reports`
- `POST /api/admin/knowledge-reports/{report_id}/approve` header `X-Admin-Key`, body `{selected_urls, resolution?, reviewer_note?}`
- `POST /api/admin/knowledge-reports/{report_id}/reject` body `{reviewer_note?, resolution?}`

**report_id:** `KR-YYYYMMDD-XXXX` random 4 alnum.

**Ingest wiring in dependencies:** build shared Firecrawl＋embeddings＋rag collection for IngestService (same as script) injected into KnowledgeReportService.

**Tests:** mock repo/ingest；approve success／fail；reject；create list；admin key missing；tool without user id fails gracefully.

**Do not commit yet** (controller will commit+push). Check off tasks 1.x–2.x.

Verify:
```
.venv/bin/python -m pytest -c pytest.ini tests/unit/services/knowledge_reports tests/unit/repositories/test_knowledge_report* tests/unit/routers -q --tb=line -k knowledge 2>/dev/null; \
.venv/bin/python -m pytest -c pytest.ini tests/unit/tools/test_registry.py tests/unit/services/knowledge_reports tests/unit/tools/test_knowledge_report* -q --tb=short
```

---

### Task 2: LIFF

Under `/Users/jamessu/Desktop/computersciencehomework/CARE-LIFF`:

- Add `src/api/knowledgeReportsApi.ts` — GET list with authHeaders
- Update `KnowledgeReports/index.tsx` — load on mount；map API statuses；empty／error state；keep i18n labels
- Update `src/tests/knowledgeReports.test.tsx` — mock fetch API

**Do not commit LIFF unless easy on current branch.** Check off 3.x.

---

### Task 3: Finalize CARE push

Controller: commit CARE knowledge-reports + push `origin jamesbranch`.
