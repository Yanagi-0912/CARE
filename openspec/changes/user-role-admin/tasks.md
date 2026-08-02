## 1. Model／Repo／Auth

- [x] 1.1 UserProfile 加 `role`；upsert 預設 `user`
- [x] 1.2 `require_admin_user`；移除 API key 驗證與 config
- [x] 1.3 Admin knowledge-reports router 改依賴 JWT＋admin
- [x] 1.4 `scripts/backfill_user_roles.py`（既有 users → admin）
- [x] 1.5 更新單元測試

## 2. 收尾

- [x] 2.1 跑相關 pytest；對現有 DB 執行 backfill
- [x] 2.2 勾選 tasks；commit＋push `jamesbranch`
