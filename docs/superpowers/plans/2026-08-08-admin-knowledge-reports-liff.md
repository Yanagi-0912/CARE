# Admin Knowledge Reports LIFF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CARE-LIFF Admin 頁審核自動／手動知識回報：列表、一鍵核准、拒絕。

**Architecture:** LIFF `AdminRoute`（`role` from `/api/profiles/me`）→ Admin page → 既有 CARE admin knowledge-reports API。

**Tech Stack:** React、Vitest、i18next；後端不改。

**OpenSpec:** `CARE/openspec/changes/admin-knowledge-reports-liff/`

## Global Constraints

- Work primarily in `/Users/jamessu/Desktop/computersciencehomework/CARE-LIFF`
- Reuse KnowledgeReports UI patterns；minimal new CSS
- Approve with `{}` body（server uses `user_source_urls`）
- Do not commit unless asked
- Mark OpenSpec tasks in CARE repo when done

---

### Task 1: API + role typing

Under CARE-LIFF:

- Extend `HealthProfile` with `role?: 'admin' | 'user'`
- Add to `knowledgeReportsApi.ts`:
  - `fetchAdminKnowledgeReports(status?: string)`
  - `approveKnowledgeReport(reportId, body?)`
  - `rejectKnowledgeReport(reportId, body?)`
- Keep `authHeaders` pattern

Verify typecheck / existing tests still pass.

---

### Task 2: AdminRoute + nav

- Create `src/components/AdminRoute` or `src/routes/AdminRoute.tsx`: fetch profile, if not admin navigate to `/`
- Wire in `App.tsx`: `<ProtectedRoute><AdminRoute><AdminKnowledgeReportsPage /></AdminRoute></ProtectedRoute>`
- Sidebar: show link only when admin（fetch role once or lift to small hook）
- i18n keys `adminKnowledgeReports.*` / sidebar label
- Add path to `liffState` allowed paths if needed

---

### Task 3: Admin page

- `src/pages/AdminKnowledgeReports/index.tsx` (+ css)
- Load admin list；tabs pending／reviewing／all(queue)
- Dialog: question, reason, user_note, source URLs (links), Approve / Reject buttons
- Optional reviewer_note input (simple)
- On success: reload list, close dialog
- Show API errors

---

### Task 4: Tests

- `src/tests/adminKnowledgeReports.test.tsx`
- Mock admin APIs；assert approve/reject called
- Mock non-admin AdminRoute redirect behavior if testable
- Run `npm test` / vitest targeted

Check off OpenSpec `tasks.md` 1–4.
