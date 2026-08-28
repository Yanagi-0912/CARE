"""角色型邀請的四道限制。

邀請連結可以被轉發，因此四條路都要堵死：

1. 角色在**建立當下**存進邀請記錄，`accept` 忽略客戶端帶來的角色。
2. 指向他人的照護圈需要該擁有者的有效委任。
3. `GUARDIAN` 僅擁有者本人可指定。
4. 邀請只作用於尚非成員者——否則能建立邀請的人只要把自己「重新加入」一次
   就完成提權，前三道全部繞過。

repository 以注入的假物件替代（不使用 monkey patch）。
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.models.family_tree import FamilyMember, FamilyTree, PendingInvitation
from app.services.family.family_tree_service import FamilyTreeService

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
OWNER = "U-owner"
INVITER = "U-inviter"
INVITEE = "U-invitee"


class FakeRepo:
    def __init__(self, trees=None):
        self.trees = trees or {}
        self.saved = []
        self.added = []
        self.accepted = []
        self.invitations = {}

    async def save_invitation(
        self, token, inviter_id, expires_at, owner_id=None, family_role=None
    ):
        invitation = PendingInvitation(
            _id=token,
            inviter_id=inviter_id,
            owner_id=owner_id or inviter_id,
            family_role=family_role,
            created_at=NOW,
            expires_at=expires_at,
        )
        self.saved.append(invitation)
        self.invitations[token] = invitation
        return invitation

    async def get_invitation(self, invite_id):
        return self.invitations.get(invite_id)

    async def get_by_user_id(self, user_id):
        return self.trees.get(user_id)

    async def upsert_tree(self, user_id):
        return self.trees.setdefault(
            user_id,
            FamilyTree(user_id=user_id, family_members=[], created_at=NOW, updated_at=NOW),
        )

    async def add_member(self, user_id, member):
        self.added.append((user_id, member))
        tree = await self.upsert_tree(user_id)
        if not any(m.user_id == member.user_id for m in tree.family_members):
            tree.family_members.append(member)
        return tree

    async def accept_invitation(self, invite_id):
        self.accepted.append(invite_id)


class FakeAuthz:
    def __init__(self, delegates=None):
        self.delegates = delegates or set()

    async def is_active_delegate(self, operator_id, owner_id):
        return (operator_id, owner_id) in self.delegates


def make_service(trees=None, delegates=None):
    repo = FakeRepo(trees)
    return FamilyTreeService(repository=repo), repo, FakeAuthz(delegates)


def tree(owner_id, members=None):
    return FamilyTree(
        user_id=owner_id,
        family_members=members or [],
        created_at=NOW,
        updated_at=NOW,
    )


# ── 建立邀請的資格 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invite_defaults_to_own_circle_without_role():
    """省略兩個欄位時行為與變更前完全相同。"""
    service, repo, authz = make_service()
    invitation = await service.create_invitation(INVITER, authorization_service=authz)
    assert invitation.target_owner_id == INVITER
    assert invitation.family_role is None


@pytest.mark.asyncio
async def test_owner_may_invite_as_guardian_into_own_circle():
    service, repo, authz = make_service()
    invitation = await service.create_invitation(
        OWNER, owner_id=OWNER, family_role="GUARDIAN", authorization_service=authz
    )
    assert invitation.family_role == "GUARDIAN"


@pytest.mark.asyncio
async def test_non_delegate_cannot_invite_into_someone_elses_circle():
    """否則任何人都能把陌生人塞進長輩的照護圈。"""
    service, repo, authz = make_service()
    with pytest.raises(HTTPException) as exc:
        await service.create_invitation(
            INVITER, owner_id=OWNER, family_role="MEMBER", authorization_service=authz
        )
    assert exc.value.status_code == 403
    assert repo.saved == []


@pytest.mark.asyncio
async def test_delegate_may_invite_as_caregiver():
    service, repo, authz = make_service(delegates={(INVITER, OWNER)})
    invitation = await service.create_invitation(
        INVITER, owner_id=OWNER, family_role="CAREGIVER", authorization_service=authz
    )
    assert invitation.target_owner_id == OWNER
    assert invitation.family_role == "CAREGIVER"


@pytest.mark.asyncio
async def test_delegate_cannot_invite_as_guardian():
    """只有擁有者本人能造出 GUARDIAN，否則委任鏈就成立了。"""
    service, repo, authz = make_service(delegates={(INVITER, OWNER)})
    with pytest.raises(HTTPException) as exc:
        await service.create_invitation(
            INVITER, owner_id=OWNER, family_role="GUARDIAN", authorization_service=authz
        )
    assert exc.value.status_code == 403
    assert repo.saved == []


@pytest.mark.asyncio
async def test_owner_role_cannot_be_invited():
    service, repo, authz = make_service()
    with pytest.raises(HTTPException) as exc:
        await service.create_invitation(
            OWNER, owner_id=OWNER, family_role="OWNER", authorization_service=authz
        )
    assert exc.value.status_code == 400
    assert repo.saved == []


@pytest.mark.asyncio
async def test_unknown_role_cannot_be_invited():
    service, repo, authz = make_service()
    with pytest.raises(HTTPException) as exc:
        await service.create_invitation(
            OWNER, family_role="SUPERUSER", authorization_service=authz
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_missing_authorization_service_fails_closed():
    """沒有授權服務就無從判定資格——拒絕，不放行。"""
    service, repo, _ = make_service()
    with pytest.raises(HTTPException) as exc:
        await service.create_invitation(INVITER, owner_id=OWNER, family_role="MEMBER")
    assert exc.value.status_code == 403


# ── 接受邀請 ──────────────────────────────────────────────────────────


async def _accept(service, repo, invitation, invitee=INVITEE):
    repo.invitations[invitation.id] = invitation
    return await service.accept_invitation(invitee, invitation.id)


@pytest.mark.asyncio
async def test_role_comes_from_the_invitation_record_not_the_request():
    """角色由伺服器保存。接受邀請的請求連帶不帶角色都不影響結果。"""
    service, repo, authz = make_service({OWNER: tree(OWNER)})
    invitation = await service.create_invitation(
        OWNER, owner_id=OWNER, family_role="CAREGIVER", authorization_service=authz
    )
    status, _ = await _accept(service, repo, invitation)
    assert status == "joined"
    owner_side = [m for o, m in repo.added if o == OWNER]
    assert owner_side[0].family_role == "CAREGIVER"


@pytest.mark.asyncio
async def test_role_is_written_one_way_only():
    """受邀者從未表示要授予擁有者任何權限，反向那筆維持未設定。"""
    service, repo, authz = make_service({OWNER: tree(OWNER)})
    invitation = await service.create_invitation(
        OWNER, owner_id=OWNER, family_role="GUARDIAN", authorization_service=authz
    )
    await _accept(service, repo, invitation)
    invitee_side = [m for o, m in repo.added if o == INVITEE]
    assert invitee_side[0].user_id == OWNER
    assert invitee_side[0].family_role is None


@pytest.mark.asyncio
async def test_invitation_without_role_joins_as_unset_member():
    service, repo, authz = make_service({OWNER: tree(OWNER)})
    invitation = await service.create_invitation(OWNER, authorization_service=authz)
    await _accept(service, repo, invitation)
    owner_side = [m for o, m in repo.added if o == OWNER]
    assert owner_side[0].family_role is None
    assert owner_side[0].effective_family_role == "MEMBER"


@pytest.mark.asyncio
async def test_existing_member_keeps_their_role():
    """邀請 SHALL NOT 作用於既有成員。

    否則能建立邀請的人只要對自己發一張 GUARDIAN 邀請再接受，就完成提權——
    前面三道限制全部被繞過。
    """
    service, repo, authz = make_service(
        {OWNER: tree(OWNER, [FamilyMember(user_id=INVITEE, family_role="MEMBER")])}
    )
    invitation = await service.create_invitation(
        OWNER, owner_id=OWNER, family_role="GUARDIAN", authorization_service=authz
    )
    status, message = await _accept(service, repo, invitation)
    assert status == "already_member"
    assert repo.added == []
    existing = repo.trees[OWNER].family_members[0]
    assert existing.family_role == "MEMBER"


@pytest.mark.asyncio
async def test_delegate_created_invitation_joins_the_owners_circle():
    """受委任者建立的邀請，受邀者加入的是**擁有者**的照護圈，不是委任者的。"""
    service, repo, authz = make_service(
        {OWNER: tree(OWNER)}, delegates={(INVITER, OWNER)}
    )
    invitation = await service.create_invitation(
        INVITER, owner_id=OWNER, family_role="CAREGIVER", authorization_service=authz
    )
    await _accept(service, repo, invitation)
    assert {o for o, _ in repo.added} == {OWNER, INVITEE}


@pytest.mark.asyncio
async def test_cannot_invite_yourself():
    service, repo, authz = make_service({OWNER: tree(OWNER)})
    invitation = await service.create_invitation(OWNER, authorization_service=authz)
    with pytest.raises(HTTPException) as exc:
        await _accept(service, repo, invitation, invitee=OWNER)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_legacy_invitation_without_owner_id_still_works():
    """舊資料沒有 owner_id，target_owner_id 落回邀請者本人。"""
    invitation = PendingInvitation(
        _id="legacy-token",
        inviter_id=INVITER,
        created_at=NOW,
        expires_at=NOW + timedelta(days=7),
    )
    assert invitation.target_owner_id == INVITER
    assert invitation.family_role is None
