"""角色指派的六道提權防護。

每一道各自封死一條路，順序有意義：**「有沒有資格碰這份文件」永遠先於「要對
這份文件做什麼」**。因此這裡不只斷言結果，也斷言「資格未過時完全沒有讀取目標
族譜」——先讀再判斷，就會出現「用目標族譜的內容決定要不要放行」的寫法，而那
份內容正是被管理的對象。
"""

from datetime import datetime, timezone
from typing import Optional

import pytest
from fastapi import HTTPException

from app.models.family_tree import FamilyMember, FamilyTree
from app.services.family.family_role_service import FamilyRoleService

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
OWNER = "U-owner"
OPERATOR = "U-operator"
MEMBER = "U-member"
STRANGER = "U-stranger"


class RecordingTreeRepository:
    """記錄自己被讀過幾次，用來驗證「資格先於讀取」。"""

    def __init__(self, trees=None):
        self.trees = trees or {}
        self.reads = []
        self.role_writes = []

    async def get_by_user_id(self, user_id):
        self.reads.append(user_id)
        return self.trees.get(user_id)

    async def set_family_role(self, owner_id, member_id, family_role):
        self.role_writes.append((owner_id, member_id, family_role))
        tree = self.trees.get(owner_id)
        if tree is None:
            return None
        for m in tree.family_members:
            if m.user_id == member_id:
                m.family_role = family_role
                return tree
        return None


class FakeAuthz:
    """只回答「是不是有效受委任者」與「指派完成狀態」。"""

    def __init__(self, delegates=None, status=None):
        self.delegates = delegates or set()
        self.status = status

    async def is_active_delegate(self, operator_id, owner_id):
        return (operator_id, owner_id) in self.delegates

    async def role_assignment_status(self, owner_id):
        return self.status


class RecordingAudit:
    def __init__(self):
        self.entries = []

    async def append(self, **kwargs):
        self.entries.append(kwargs)
        return kwargs


def make_tree(owner_id, members, state="enforced"):
    return FamilyTree(
        user_id=owner_id,
        family_members=members,
        rbac_migration_state=state,
        created_at=NOW,
        updated_at=NOW,
    )


def make_service(trees=None, delegates=None):
    repo = RecordingTreeRepository(trees)
    audit = RecordingAudit()
    service = FamilyRoleService(
        authorization_service=FakeAuthz(delegates),
        family_tree_repository=repo,
        audit_repository=audit,
    )
    return service, repo, audit


def owner_tree_with(member_role: Optional[str] = None):
    return {
        OWNER: make_tree(
            OWNER, [FamilyMember(user_id=MEMBER, family_role=member_role)]
        )
    }


# ── 檢查 1：資格先於讀取 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_owner_can_assign_in_own_tree():
    service, repo, audit = make_service(owner_tree_with())
    tree = await service.assign_role(OWNER, OWNER, MEMBER, "GUARDIAN")
    assert tree.family_members[0].family_role == "GUARDIAN"
    assert repo.role_writes == [(OWNER, MEMBER, "GUARDIAN")]


@pytest.mark.asyncio
async def test_non_delegate_cannot_assign_in_someone_elses_tree():
    """未受委任者沒有指向他人族譜的路徑。"""
    service, repo, _ = make_service(owner_tree_with())
    with pytest.raises(HTTPException) as exc:
        await service.assign_role(OPERATOR, OWNER, MEMBER, "GUARDIAN")
    assert exc.value.status_code == 403
    assert repo.role_writes == []


@pytest.mark.asyncio
async def test_ineligible_caller_never_reads_the_target_tree():
    """資格未過時，目標族譜連讀都不該讀。

    先讀再判斷會導向「用目標族譜的內容決定要不要放行」，而那份內容正是被
    管理的對象。資格必須來自呼叫者與擁有者的關係。
    """
    service, repo, _ = make_service(owner_tree_with())
    with pytest.raises(HTTPException):
        await service.assign_role(OPERATOR, OWNER, MEMBER, "CAREGIVER")
    assert repo.reads == [], f"資格未過就讀了族譜：{repo.reads}"


@pytest.mark.asyncio
async def test_guardian_by_assignment_is_not_a_delegate():
    """擁有者親自指派的 GUARDIAN 不能代為管理角色。

    資料權限與「能不能代擁有者行事」是兩個問題；混用會直接產生提權路徑。
    """
    service, repo, _ = make_service(
        {OWNER: make_tree(OWNER, [
            FamilyMember(user_id=OPERATOR, family_role="GUARDIAN"),
            FamilyMember(user_id=MEMBER, family_role="MEMBER"),
        ])}
    )
    with pytest.raises(HTTPException) as exc:
        await service.assign_role(OPERATOR, OWNER, MEMBER, "GUARDIAN")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_delegate_can_assign_in_owners_tree():
    service, repo, audit = make_service(
        owner_tree_with(), delegates={(OPERATOR, OWNER)}
    )
    await service.assign_role(OPERATOR, OWNER, MEMBER, "CAREGIVER")
    assert repo.role_writes == [(OWNER, MEMBER, "CAREGIVER")]
    assert audit.entries[0]["via_delegation"] is True


# ── 檢查 3：OWNER 保護 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cannot_assign_a_role_to_the_owner_themselves():
    """OWNER 是推導值，沒有可修改的對象。

    允許改它等於允許擁有者把自己降級，然後整份資料沒有人管得動。
    """
    service, repo, _ = make_service(owner_tree_with())
    with pytest.raises(HTTPException) as exc:
        await service.assign_role(OWNER, OWNER, OWNER, "MEMBER")
    assert exc.value.status_code == 400
    assert repo.role_writes == []


@pytest.mark.asyncio
async def test_delegate_cannot_demote_the_owner_either():
    service, repo, _ = make_service(owner_tree_with(), delegates={(OPERATOR, OWNER)})
    with pytest.raises(HTTPException) as exc:
        await service.assign_role(OPERATOR, OWNER, OWNER, "MEMBER")
    assert exc.value.status_code == 400
    assert repo.role_writes == []


# ── 檢查 4：OWNER 不是可指派的值 ───────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("actor,delegates", [(OWNER, set()), (OPERATOR, {("U-operator", "U-owner")})])
async def test_owner_is_never_an_assignable_role(actor, delegates):
    service, repo, _ = make_service(owner_tree_with(), delegates=delegates)
    with pytest.raises(HTTPException) as exc:
        await service.assign_role(actor, OWNER, MEMBER, "OWNER")
    assert exc.value.status_code == 400
    assert repo.role_writes == []


@pytest.mark.asyncio
async def test_unknown_role_is_rejected_with_400():
    service, repo, _ = make_service(owner_tree_with())
    with pytest.raises(HTTPException) as exc:
        await service.assign_role(OWNER, OWNER, MEMBER, "SUPERUSER")
    assert exc.value.status_code == 400
    assert repo.role_writes == []


# ── 檢查 5：受委任者不得授予 GUARDIAN ──────────────────────────────


@pytest.mark.asyncio
async def test_delegate_cannot_grant_guardian():
    """否則委任鏈就成立了：受委任者造一個 GUARDIAN，那個人再造下一個。"""
    service, repo, _ = make_service(owner_tree_with(), delegates={(OPERATOR, OWNER)})
    with pytest.raises(HTTPException) as exc:
        await service.assign_role(OPERATOR, OWNER, MEMBER, "GUARDIAN")
    assert exc.value.status_code == 403
    assert repo.role_writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["CAREGIVER", "MEMBER"])
async def test_delegate_may_grant_caregiver_and_member(role):
    service, repo, _ = make_service(owner_tree_with(), delegates={(OPERATOR, OWNER)})
    await service.assign_role(OPERATOR, OWNER, MEMBER, role)
    assert repo.role_writes == [(OWNER, MEMBER, role)]


@pytest.mark.asyncio
async def test_owner_may_grant_guardian():
    service, repo, _ = make_service(owner_tree_with())
    await service.assign_role(OWNER, OWNER, MEMBER, "GUARDIAN")
    assert repo.role_writes == [(OWNER, MEMBER, "GUARDIAN")]


# ── 檢查 2：成員必須在該族譜內 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_member_not_in_tree_returns_404():
    service, repo, _ = make_service(owner_tree_with())
    with pytest.raises(HTTPException) as exc:
        await service.assign_role(OWNER, OWNER, "U-nobody", "MEMBER")
    assert exc.value.status_code == 404
    assert repo.role_writes == []


@pytest.mark.asyncio
async def test_missing_tree_returns_404():
    service, _, _ = make_service({})
    with pytest.raises(HTTPException) as exc:
        await service.assign_role(OWNER, OWNER, MEMBER, "MEMBER")
    assert exc.value.status_code == 404


# ── cross-family ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delegation_does_not_cross_families():
    """對甲的委任 SHALL NOT 讓人管理乙的家庭。

    「他是受委任者」不是一種全域身分——每一筆委任只對它記載的那一位擁有者
    生效。
    """
    other_owner = "U-other-owner"
    service, repo, _ = make_service(
        {
            OWNER: make_tree(OWNER, [FamilyMember(user_id=MEMBER)]),
            other_owner: make_tree(other_owner, [FamilyMember(user_id=MEMBER)]),
        },
        delegates={(OPERATOR, OWNER)},
    )
    await service.assign_role(OPERATOR, OWNER, MEMBER, "CAREGIVER")
    with pytest.raises(HTTPException) as exc:
        await service.assign_role(OPERATOR, other_owner, MEMBER, "CAREGIVER")
    assert exc.value.status_code == 403


# ── 稽核 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_records_before_and_after_and_who():
    service, _, audit = make_service(owner_tree_with("MEMBER"))
    await service.assign_role(OWNER, OWNER, MEMBER, "GUARDIAN")
    entry = audit.entries[0]
    assert entry["owner_id"] == OWNER
    assert entry["member_id"] == MEMBER
    assert entry["from_role"] == "MEMBER"
    assert entry["to_role"] == "GUARDIAN"
    assert entry["changed_by"] == OWNER
    assert entry["via_delegation"] is False


@pytest.mark.asyncio
async def test_unset_previous_role_is_recorded_as_none_not_member():
    """稽核要看得出「本來沒設定」與「本來就是 MEMBER」的差別。"""
    service, _, audit = make_service(owner_tree_with(None))
    await service.assign_role(OWNER, OWNER, MEMBER, "CAREGIVER")
    assert audit.entries[0]["from_role"] is None


@pytest.mark.asyncio
async def test_no_audit_written_when_assignment_is_rejected():
    service, _, audit = make_service(owner_tree_with())
    with pytest.raises(HTTPException):
        await service.assign_role(OWNER, OWNER, MEMBER, "OWNER")
    assert audit.entries == []


# ── 角色清單與指派狀態 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_roles_requires_management_rights():
    """「誰有什麼權限」本身就是管理資訊，不對一般成員開放。"""
    service, _, _ = make_service(owner_tree_with())
    with pytest.raises(HTTPException) as exc:
        await service.list_roles(STRANGER, OWNER)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_list_roles_distinguishes_unset_from_member():
    service, _, _ = make_service(
        {
            OWNER: make_tree(
                OWNER,
                [
                    FamilyMember(user_id="U-a", family_role="MEMBER"),
                    FamilyMember(user_id="U-b"),
                ],
            )
        }
    )
    entries = await service.list_roles(OWNER, OWNER)
    by_id = {e.user_id: e for e in entries}
    assert by_id["U-a"].family_role == "MEMBER"
    assert by_id["U-b"].family_role is None
    assert by_id["U-b"].effective_family_role == "MEMBER"


@pytest.mark.asyncio
async def test_assignment_status_requires_management_rights():
    service, _, _ = make_service(owner_tree_with())
    with pytest.raises(HTTPException):
        await service.assignment_status(STRANGER, OWNER)
