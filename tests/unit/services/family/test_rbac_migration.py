"""擁有者什麼時候從影子模式切到強制。

規則只有一條：擁有者替**每一位**成員都做了決定（明確指派了角色）的那一刻才切，
而且只往前、不回退。這裡釘住規則本身，以及觸發它的兩條路徑（指派、移除）與
回報給前端的「實際生效」狀態。
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.family_tree import FamilyMember, FamilyTree
from app.services.family.family_authorization_service import FamilyAuthorizationService
from app.services.family.family_role_service import FamilyRoleService
from app.services.family.rbac_migration import enforce_if_assignment_complete

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
OWNER = "U-owner"


def make_tree(members, state="shadow", owner=OWNER) -> FamilyTree:
    return FamilyTree(
        user_id=owner,
        family_members=members,
        rbac_migration_state=state,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeTrees:
    def __init__(self, trees=None, fail_state_write=False):
        self.trees = trees or {}
        self.state_writes = []
        self._fail_state_write = fail_state_write

    async def get_by_user_id(self, user_id):
        return self.trees.get(user_id)

    async def set_migration_state(self, user_id, state):
        if self._fail_state_write:
            raise RuntimeError("mongo down")
        self.state_writes.append((user_id, state))
        self.trees[user_id].rbac_migration_state = state
        return self.trees[user_id]

    async def set_family_role(self, owner_id, member_id, family_role):
        tree = self.trees.get(owner_id)
        for member in tree.family_members if tree else []:
            if member.user_id == member_id:
                member.family_role = family_role
                return tree
        return None


# ── 規則本身 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_switches_once_every_member_has_a_role():
    trees = FakeTrees(
        {
            OWNER: make_tree(
                [
                    FamilyMember(user_id="U-a", family_role="GUARDIAN"),
                    FamilyMember(user_id="U-b", family_role="CAREGIVER"),
                ]
            )
        }
    )

    assert await enforce_if_assignment_complete(trees, OWNER) is True
    assert trees.state_writes == [(OWNER, "enforced")]


@pytest.mark.asyncio
async def test_explicit_member_role_counts_as_a_decision():
    """明確選「一般家人」是決定過了；缺欄位才是還沒決定。"""
    trees = FakeTrees({OWNER: make_tree([FamilyMember(user_id="U-a", family_role="MEMBER")])})

    assert await enforce_if_assignment_complete(trees, OWNER) is True


@pytest.mark.asyncio
async def test_stays_shadow_while_anyone_is_unassigned():
    trees = FakeTrees(
        {
            OWNER: make_tree(
                [
                    FamilyMember(user_id="U-a", family_role="GUARDIAN"),
                    FamilyMember(user_id="U-b"),
                ]
            )
        }
    )

    assert await enforce_if_assignment_complete(trees, OWNER) is False
    assert trees.state_writes == []


@pytest.mark.asyncio
async def test_empty_family_is_not_switched():
    trees = FakeTrees({OWNER: make_tree([])})

    assert await enforce_if_assignment_complete(trees, OWNER) is False
    assert trees.state_writes == []


@pytest.mark.asyncio
async def test_missing_tree_is_ignored():
    assert await enforce_if_assignment_complete(FakeTrees({}), OWNER) is False


@pytest.mark.asyncio
async def test_never_switches_back_to_shadow():
    """新成員加入、還沒指派 → 完成狀態回到未完成，但強制不回退。"""
    trees = FakeTrees(
        {
            OWNER: make_tree(
                [
                    FamilyMember(user_id="U-a", family_role="GUARDIAN"),
                    FamilyMember(user_id="U-newcomer"),
                ],
                state="enforced",
            )
        }
    )

    assert await enforce_if_assignment_complete(trees, OWNER) is False
    assert trees.state_writes == []
    assert trees.trees[OWNER].rbac_migration_state == "enforced"


# ── 觸發路徑：指派 ────────────────────────────────────────────────────


def make_role_service(trees: FakeTrees) -> FamilyRoleService:
    authz = MagicMock()
    authz.is_active_delegate = AsyncMock(return_value=False)
    audit = MagicMock()
    audit.append = AsyncMock()
    return FamilyRoleService(
        authorization_service=authz,
        family_tree_repository=trees,
        audit_repository=audit,
    )


@pytest.mark.asyncio
async def test_assigning_the_last_member_switches_the_owner():
    trees = FakeTrees(
        {
            OWNER: make_tree(
                [
                    FamilyMember(user_id="U-a", family_role="GUARDIAN"),
                    FamilyMember(user_id="U-b"),
                ]
            )
        }
    )

    await make_role_service(trees).assign_role(OWNER, OWNER, "U-b", "CAREGIVER")

    assert trees.state_writes == [(OWNER, "enforced")]


@pytest.mark.asyncio
async def test_assigning_while_others_remain_unset_keeps_shadow():
    trees = FakeTrees(
        {OWNER: make_tree([FamilyMember(user_id="U-a"), FamilyMember(user_id="U-b")])}
    )

    await make_role_service(trees).assign_role(OWNER, OWNER, "U-a", "GUARDIAN")

    assert trees.state_writes == []


@pytest.mark.asyncio
async def test_switch_failure_does_not_undo_the_assignment():
    """角色已經寫進去了；切換失敗留給下一次指派或 backfill 腳本補。"""
    trees = FakeTrees(
        {OWNER: make_tree([FamilyMember(user_id="U-a")])}, fail_state_write=True
    )

    tree = await make_role_service(trees).assign_role(OWNER, OWNER, "U-a", "GUARDIAN")

    assert tree.family_members[0].family_role == "GUARDIAN"


# ── 回報給前端的狀態 ─────────────────────────────────────────────────


def make_authz(tree: FamilyTree, enforcement_enabled: bool) -> FamilyAuthorizationService:
    return FamilyAuthorizationService(
        family_tree_repository=FakeTrees({OWNER: tree}),
        delegation_repository=MagicMock(),
        enforcement_enabled=enforcement_enabled,
    )


@pytest.mark.asyncio
async def test_status_reports_shadow_when_the_kill_switch_is_off():
    """總閘關閉時，存著 enforced 也不生效——前端要照實告訴擁有者「大家都看得到」。"""
    tree = make_tree([FamilyMember(user_id="U-a", family_role="GUARDIAN")], state="enforced")

    status = await make_authz(tree, enforcement_enabled=False).role_assignment_status(OWNER)

    assert status.rbac_migration_state == "shadow"


@pytest.mark.asyncio
async def test_status_reports_enforced_when_both_switches_are_on():
    tree = make_tree([FamilyMember(user_id="U-a", family_role="GUARDIAN")], state="enforced")

    status = await make_authz(tree, enforcement_enabled=True).role_assignment_status(OWNER)

    assert status.rbac_migration_state == "enforced"


class ExplodingTrees:
    async def get_by_user_id(self, user_id):
        raise AssertionError("本人存取不該讀族譜")


@pytest.mark.asyncio
async def test_own_data_does_not_read_the_migration_state_with_the_switch_on():
    """總閘打開後，本人存取若還去讀遷移狀態，就是每支端點多一趟族譜查詢。"""
    service = FamilyAuthorizationService(
        family_tree_repository=ExplodingTrees(),
        delegation_repository=MagicMock(),
        enforcement_enabled=True,
    )

    assert await service.authorize(OWNER, OWNER, "SENSITIVE", "WRITE") == "OWNER"
