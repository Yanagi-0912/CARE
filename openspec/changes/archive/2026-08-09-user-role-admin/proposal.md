## Why

知識回報核准不應靠共享 API key。現有使用者本來就是內部成員，改在 user 文件加 `role`，以登入 JWT 判斷是否可審核。

## What Changes

- User profile 新增 `role`：`admin` | `user`（缺省／未設視為 `user`）。
- 現有庫內使用者一次性標成 `admin`。
- 知識回報 approve／reject 改為要求 **Bearer JWT 且 role=admin**；移除 `KNOWLEDGE_REPORTS_ADMIN_API_KEY`／`X-Admin-Key`。
- 新註冊使用者預設 `role=user`（可之後再升）。

## Capabilities

### New Capabilities

- `user-roles`：使用者角色欄位與 admin 閘門。

### Modified Capabilities

- `knowledge-reports`：營運端改以 admin role JWT 核准／拒絕。

## Impact

- `app/models/user.py`、repository／upsert 預設、`dependencies`、`routers/admin/knowledge_reports.py`、config／`.env.example`、相關測試
- 一次性 DB backfill（腳本或啟動時 update）
