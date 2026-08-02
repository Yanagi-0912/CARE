## Decisions

1. **欄位** `role: "admin" | "user"`，存在 `users` 文件頂層（與 `line_id` 同層）。
2. **預設** 新建／upsert 未帶 role → `user`；讀取時缺欄位視為 `user`。
3. **Backfill** `scripts/backfill_user_roles.py`：所有目前無 role 或全部既有使用者設為 `admin`（本專案現況：全員 admin）。
4. **閘門** `require_admin_user`：`get_current_user` → 讀 profile → `role == "admin"` 否則 403。
5. **移除** `KNOWLEDGE_REPORTS_ADMIN_API_KEY` 與 `verify_knowledge_reports_admin_key`。
6. Admin routes 仍掛 `/api/admin/knowledge-reports`，改依賴 JWT＋admin role。

## Non-Goals

- 複雜 RBAC、多角色、前端角色管理 UI
