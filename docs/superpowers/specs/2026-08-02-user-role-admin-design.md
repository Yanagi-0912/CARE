# User Role Admin Design

**Date:** 2026-08-02  
**OpenSpec:** `openspec/changes/user-role-admin/`

- Field `role` on `users`: `admin` | `user`
- Existing users → admin (backfill)
- New users → user (`$setOnInsert`)
- Knowledge-report approve/reject: Bearer JWT + role=admin
- Remove shared admin API key
