# Web Fallback Auto-Pending Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CRAG web fallback 成功且白名單來源時自動建 pending 知識回報；admin 同意即可 ingest；同 URL 去重／覆蓋。

**Architecture:** `WebSearchService` 成功路徑 → `KnowledgeReportService.create_from_web_fallback` → Mongo pending；approve 預設 `user_source_urls` → `IngestService.ingest_url`（同 URL 覆蓋）。

**Tech Stack:** FastAPI、Motor、pytest；OpenSpec change `web-fallback-auto-pending`

**OpenSpec:** `openspec/changes/web-fallback-auto-pending/`

## Global Constraints

- 最小 diff；不改 LIFF／Admin UI
- 建報失敗不影響回答
- Work in CARE；對應 openspec tasks 勾選
- Commit 僅在使用者要求時

---

### Task 1: Repository / Service

Implement under `/Users/jamessu/Desktop/computersciencehomework/CARE`:

**Modify:**
- `app/repositories/knowledge_report_repository.py` — `delete_pending_or_reviewing_by_urls(urls)`
- `app/services/knowledge_reports/service.py` — `create_from_web_fallback`；`approve` 回退 URLs
- `app/models/knowledge_report.py` — `ApproveKnowledgeReportRequest.selected_urls` 可選（default empty）
- tests under `tests/unit/services/knowledge_reports/`、`tests/unit/repositories/`

**Behavior:**
- create_from_web_fallback: filter non-empty URLs → delete old pending/reviewing containing any → create pending missing + note `auto:web-fallback`
- approve: urls = selected or report.user_source_urls；empty → 400

Verify:
```
.venv/bin/python -m pytest -c pytest.ini tests/unit/services/knowledge_reports tests/unit/repositories -q --tb=short -k knowledge
```

---

### Task 2: Admin list API

**Modify:**
- `app/repositories/knowledge_report_repository.py` — list by statuses
- `app/services/knowledge_reports/service.py` — `list_for_admin`
- `app/routers/admin/knowledge_reports.py` — GET ``
- `tests/unit/routers/test_knowledge_reports.py`

Verify admin 200／非 admin 403。

---

### Task 3: WebSearchService wiring

**Modify:**
- `app/services/rag/web_search_service.py` — optional `on_success_report` / knowledge service；成功後建報
- `app/dependencies.py` — 注入
- `tests/unit/services/rag/test_web_search_service.py`

line_user_id from `get_line_user_id()`；缺失略過。

---

### Task 4: Finalize

Run broader pytest for rag + knowledge_reports；勾選 `openspec/changes/web-fallback-auto-pending/tasks.md`。
