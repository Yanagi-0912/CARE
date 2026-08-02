# User Role Admin Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development.

**Goal:** users 加 `role`；知識回報核准改 JWT＋admin；移除 API key；既有使用者標 admin。

**Work dir:** `/Users/jamessu/Desktop/computersciencehomework/CARE` on `jamesbranch`

## Global Constraints

- 不做複雜 RBAC；role 僅 `admin`|`user`
- 新使用者預設 `user`；backfill 把**目前庫裡所有 users** 設成 `admin`
- DO NOT commit（controller commits + pushes）

---

### Task 1: Implement

1. `app/models/user.py` — `role: Literal["admin","user"] = "user"` on `UserProfile`
2. Upsert path — ensure new users get `role: user` if missing (`UserProfile.from_upsert` / auth signup). Check `UserProfileService` / LIFF login create path; `$setOnInsert: {"role": "user"}` in repository upsert is safest so we don't overwrite admin on every login.
3. `UserProfileRepository.get_user_profile` — fine as-is
4. `dependencies.py`:
   - Remove `verify_knowledge_reports_admin_key` and `KNOWLEDGE_REPORTS_ADMIN_API_KEY` usage
   - Add `async def require_admin_user(current_user=Depends(get_current_user), ...) -> CurrentUser` that loads profile via UserProfileRepository/Service; if `(profile or {}).get("role") != "admin"` → 403
5. `config.py` + `.env.example` — remove `KNOWLEDGE_REPORTS_ADMIN_API_KEY`
6. `routers/admin/knowledge_reports.py` — `dependencies=[Depends(require_admin_user)]`
7. `scripts/backfill_user_roles.py` — `update_many({}, {"$set": {"role": "admin"}})` or only where missing; print count
8. Tests: replace X-Admin-Key tests with JWT admin／user mocks; test require_admin_user

Verify:
```
.venv/bin/python -m pytest -c pytest.ini tests/unit/routers/test_knowledge_reports.py tests/unit/dependencies* -q --tb=short
# plus any new role tests
```

Check off openspec tasks 1.x

---

### Task 2: Backfill live DB

Run:
```
.venv/bin/python scripts/backfill_user_roles.py
```
Confirm 5 users role=admin.

Report DONE with pytest + backfill counts. No commit.
