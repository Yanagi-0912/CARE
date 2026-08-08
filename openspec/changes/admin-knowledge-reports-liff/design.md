## Context

後端已有 `require_admin_user` + `GET/POST approve/reject /api/admin/knowledge-reports`。LIFF 僅有使用者追蹤頁 `/knowledge-reports`，auth 只檢查登入、不讀 `role`。

實作 repo：sibling `/Users/jamessu/Desktop/computersciencehomework/CARE-LIFF`（OpenSpec 留在 CARE 記錄產品契約）。

## Goals / Non-Goals

**Goals:**
- Admin 登入後可看待審佇列、核准（用報告既有 URL）、拒絕
- 非 admin 無法進入審核路由

**Non-Goals:**
- 完整後台、URL 多選編輯、ingest 進度細節、resolved 歷史管理、升權 UI

## Decisions

1. **角色來源**：`getPersonalHealthProfile()` → 擴充型別含 `role?: 'admin' | 'user'`；`AdminRoute` 載入後檢查，非 admin → 導向 `/` 或顯示無權限。
2. **路由**：`/admin/knowledge-reports`，包在 `ProtectedRoute` + `AdminRoute`。
3. **API client**：`knowledgeReportsApi.ts` 新增 `fetchAdminKnowledgeReports`、`approveKnowledgeReport`、`rejectKnowledgeReport`；approve body 預設 `{}`。
4. **UI**：複用使用者 KnowledgeReports 的 tabs／card／dialog 風格；dialog 加 Approve／Reject；顯示 `user_source_urls`、`user_note`、question。
5. **入口**：Sidebar（與可選 Home）僅 `role===admin` 時顯示連結。
6. **測試**：Vitest mock API；admin 可見操作、非 admin 被擋。

## Risks / Trade-offs

- [前端 role 可被繞過] → 後端仍 403；前端僅 UX
- [profile 無 role 欄位舊資料] → 視同 user
- [跨 repo OpenSpec] → design／tasks 明確寫 CARE-LIFF 路徑

## Migration Plan

部署 LIFF 後，將營運帳號 profile `role` 設為 `admin` 即可使用。無需 DB migration。

## Open Questions

- （無；v1 不做 URL picker）
