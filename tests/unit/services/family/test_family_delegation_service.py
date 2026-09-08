"""委任服務：閘門、家庭邊界、撤銷權與稽核。

最重要的一條是閘門：核可流程（身分驗證、醫療證明、法定監護證明）由後續的
產品／法務 change 定義，在那之前建立委任的路徑一律回 404，表現得像這個功能
不存在。這件事要在測試裡是顯性的——委任的資料模型與權限邊界都寫好了，很容易
讓人以為問題已經解決，實際上開放與否取決於一份還不存在的流程。
"""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.models.family_tree import FamilyMember, FamilyTree
from app.services.family.family_delegation_service import FamilyDelegationService

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
OWNER = "U-owner"
DELEGATE = "U-delegate"
OUTSIDER = "U-outsider"


class FakeDelegationRepo:
    def __init__(self):
        self.granted = []
        self.revoked = []
        self.active = []

    async def grant(self, owner_id, delegate_user_id, granted_by, approval_ref=None, valid_days=90):
        record = {
            "owner_id": owner_id,
            "delegate_user_id": delegate_user_id,
            "granted_by": granted_by,
            "approval_ref": approval_ref,
            "valid_days": valid_days,
        }
        self.granted.append(record)
        return record

    async def revoke(self, owner_id, delegate_user_id, revoked_by):
        self.revoked.append((owner_id, delegate_user_id, revoked_by))
        return 1

    async def list_active(self, owner_id):
        return self.active


class FakeTreeRepo:
    def __init__(self, trees=None):
        self.trees = trees or {}

    async def get_by_user_id(self, user_id):
        return self.trees.get(user_id)


class RecordingAudit:
    def __init__(self):
        self.entries = []

    async def append(self, **kwargs):
        self.entries.append(kwargs)
        return kwargs


def make_tree(owner_id, members):
    return FamilyTree(
        user_id=owner_id, family_members=members, created_at=NOW, updated_at=NOW
    )


def make_service(activation_enabled=False, trees=None):
    delegations = FakeDelegationRepo()
    audit = RecordingAudit()
    service = FamilyDelegationService(
        delegation_repository=delegations,
        family_tree_repository=FakeTreeRepo(
            trees
            if trees is not None
            else {OWNER: make_tree(OWNER, [FamilyMember(user_id=DELEGATE)])}
        ),
        audit_repository=audit,
        activation_enabled=activation_enabled,
    )
    return service, delegations, audit


# ── 閘門 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grant_is_closed_until_the_approval_process_is_defined():
    """核可流程確定之前，建立委任的路徑表現得像不存在一樣。"""
    service, delegations, _ = make_service(activation_enabled=False)
    with pytest.raises(HTTPException) as exc:
        await service.grant(OWNER, DELEGATE, granted_by="U-approver")
    assert exc.value.status_code == 404
    assert delegations.granted == []


@pytest.mark.asyncio
async def test_grant_works_once_activation_is_enabled():
    """閘門開啟後，其餘邊界（家庭成員、90 天效期）照常成立。"""
    service, delegations, audit = make_service(activation_enabled=True)
    await service.grant(OWNER, DELEGATE, granted_by="U-approver")
    assert delegations.granted[0]["owner_id"] == OWNER
    assert delegations.granted[0]["valid_days"] == 90
    assert audit.entries[0]["event"] == "delegation_granted"
    assert audit.entries[0]["via_delegation"] is True


# ── 家庭邊界 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delegation_cannot_be_granted_to_a_non_member():
    """委任提升的是既有成員的權限，不是把陌生人放進照護圈。

    少了這道檢查，委任就成了繞過家庭邊界的入口，而家庭邊界是整套授權最外層
    的閘門。
    """
    service, delegations, _ = make_service(activation_enabled=True)
    with pytest.raises(HTTPException) as exc:
        await service.grant(OWNER, OUTSIDER, granted_by="U-approver")
    assert exc.value.status_code == 404
    assert delegations.granted == []


@pytest.mark.asyncio
async def test_delegation_cannot_be_granted_to_the_owner():
    service, delegations, _ = make_service(activation_enabled=True)
    with pytest.raises(HTTPException) as exc:
        await service.grant(OWNER, OWNER, granted_by=OWNER)
    assert exc.value.status_code == 400
    assert delegations.granted == []


@pytest.mark.asyncio
async def test_grant_on_missing_tree_is_rejected():
    service, delegations, _ = make_service(activation_enabled=True, trees={})
    with pytest.raises(HTTPException):
        await service.grant(OWNER, DELEGATE, granted_by="U-approver")
    assert delegations.granted == []


# ── 撤銷 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_owner_can_always_revoke_even_while_activation_is_closed():
    """閘門管的是能不能給出去，不是能不能收回來。

    把撤銷一起關掉，會讓已經存在的委任在流程確定之前無法解除。
    """
    service, delegations, audit = make_service(activation_enabled=False)
    revoked = await service.revoke(OWNER, OWNER, DELEGATE)
    assert revoked == 1
    assert delegations.revoked == [(OWNER, DELEGATE, OWNER)]
    assert audit.entries[0]["event"] == "delegation_revoked"


@pytest.mark.asyncio
async def test_delegate_cannot_revoke_someone_elses_delegation():
    """撤銷是收回權力的動作，只有權力的來源可以做。

    若受委任者能互相解除，而擁有者正處於無法表達意願的狀態，他既看不到也
    管不了。
    """
    service, delegations, _ = make_service(activation_enabled=True)
    with pytest.raises(HTTPException) as exc:
        await service.revoke(DELEGATE, OWNER, "U-another-delegate")
    assert exc.value.status_code == 403
    assert delegations.revoked == []


@pytest.mark.asyncio
async def test_no_audit_when_nothing_was_revoked():
    """沒有東西被撤銷時不留假紀錄，否則稽核會看到不存在的事件。"""
    service, delegations, audit = make_service(activation_enabled=True)

    async def revoke_nothing(**kwargs):
        return 0

    delegations.revoke = revoke_nothing
    assert await service.revoke(OWNER, OWNER, DELEGATE) == 0
    assert audit.entries == []


# ── 查詢 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_owner_can_list_their_delegations():
    """「誰代我行事」是擁有者的資訊，不是家庭公開資訊。"""
    service, _, _ = make_service(activation_enabled=True)
    with pytest.raises(HTTPException) as exc:
        await service.list_active(DELEGATE, OWNER)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_owner_lists_only_active_delegations():
    service, delegations, _ = make_service(activation_enabled=True)
    delegations.active = ["one"]
    assert await service.list_active(OWNER, OWNER) == ["one"]
