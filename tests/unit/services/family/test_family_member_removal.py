"""移除家人：兩個方向一起切斷，誰按都可以，動不到第三者。"""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.models.family_tree import FamilyMember, FamilyTree
from app.services.family.family_tree_service import FamilyTreeService

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
ELDER = "U-elder"
KID = "U-kid"
OTHER = "U-other"


def make_tree(owner, members, state="shadow") -> FamilyTree:
    return FamilyTree(
        user_id=owner,
        family_members=members,
        rbac_migration_state=state,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeTreeRepository:
    """行為對齊真實 repository：不在族譜內回 None，在的話回移除前的那一筆。"""

    def __init__(self, trees):
        self.trees = trees
        self.state_writes = []

    async def remove_member(self, user_id, member_id):
        tree = self.trees.get(user_id)
        if tree is None:
            return None
        for member in tree.family_members:
            if member.user_id == member_id:
                tree.family_members = [
                    m for m in tree.family_members if m.user_id != member_id
                ]
                return FamilyMember(user_id=member_id, family_role=member.family_role)
        return None

    async def get_by_user_id(self, user_id):
        return self.trees.get(user_id)

    async def set_migration_state(self, user_id, state):
        self.state_writes.append((user_id, state))
        self.trees[user_id].rbac_migration_state = state
        return self.trees[user_id]


class FakeDelegations:
    """記下撤銷了哪些 (owner, delegate)；`active` 裡有的才算真的撤到。"""

    def __init__(self, active=(), fail=False):
        self.active = set(active)
        self.revoked = []
        self._fail = fail

    async def revoke(self, owner_id, delegate_user_id, revoked_by):
        if self._fail:
            raise RuntimeError("delegations down")
        self.revoked.append((owner_id, delegate_user_id, revoked_by))
        if (owner_id, delegate_user_id) in self.active:
            self.active.discard((owner_id, delegate_user_id))
            return 1
        return 0


def make_service(repo, audit=None, delegations=None):
    return FamilyTreeService(
        repository=repo,
        audit_repository=audit,
        delegation_repository=delegations or FakeDelegations(),
    )


class RecordingAudit:
    def __init__(self, fail=False):
        self.entries = []
        self._fail = fail

    async def append(self, **kwargs):
        if self._fail:
            raise RuntimeError("audit down")
        self.entries.append(kwargs)


def linked_family():
    """長輩把孩子設為主要照顧者；孩子的族譜裡也有長輩（反向不帶角色）。"""
    return {
        ELDER: make_tree(
            ELDER,
            [
                FamilyMember(user_id=KID, family_role="GUARDIAN"),
                FamilyMember(user_id=OTHER, family_role="MEMBER"),
            ],
        ),
        KID: make_tree(KID, [FamilyMember(user_id=ELDER)]),
        OTHER: make_tree(OTHER, [FamilyMember(user_id=ELDER)]),
    }


def ids(tree: FamilyTree) -> list[str]:
    return [m.user_id for m in tree.family_members]


@pytest.mark.asyncio
async def test_removal_cuts_both_directions():
    repo = FakeTreeRepository(linked_family())

    await make_service(repo).remove_member(ELDER, KID)

    assert KID not in ids(repo.trees[ELDER])
    assert ELDER not in ids(repo.trees[KID])


@pytest.mark.asyncio
async def test_family_member_can_leave_the_elders_family():
    """同一支端點由孩子那一側按下：放棄自己的存取，不是取得權限。"""
    repo = FakeTreeRepository(linked_family())

    await make_service(repo).remove_member(KID, ELDER)

    assert KID not in ids(repo.trees[ELDER])
    assert ELDER not in ids(repo.trees[KID])


@pytest.mark.asyncio
async def test_third_parties_are_untouched():
    repo = FakeTreeRepository(linked_family())

    await make_service(repo).remove_member(ELDER, KID)

    assert ids(repo.trees[ELDER]) == [OTHER]
    assert ids(repo.trees[OTHER]) == [ELDER]


@pytest.mark.asyncio
async def test_one_sided_link_is_still_removed():
    """舊資料可能只有單向登記；拿得掉哪一邊就拿哪一邊，不回 404。"""
    trees = linked_family()
    trees[KID] = make_tree(KID, [])
    repo = FakeTreeRepository(trees)

    await make_service(repo).remove_member(ELDER, KID)

    assert KID not in ids(repo.trees[ELDER])


@pytest.mark.asyncio
async def test_removing_someone_who_is_not_family_is_404():
    repo = FakeTreeRepository(linked_family())

    with pytest.raises(HTTPException) as exc:
        await make_service(repo).remove_member(ELDER, "U-stranger")

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_removing_yourself_is_400():
    repo = FakeTreeRepository(linked_family())

    with pytest.raises(HTTPException) as exc:
        await make_service(repo).remove_member(ELDER, ELDER)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_removal_is_audited_on_both_sides_with_the_previous_role():
    repo = FakeTreeRepository(linked_family())
    audit = RecordingAudit()

    await make_service(repo, audit=audit).remove_member(ELDER, KID)

    assert audit.entries == [
        {
            "owner_id": ELDER,
            "member_id": KID,
            "changed_by": ELDER,
            "from_role": "GUARDIAN",
            "to_role": None,
            "event": "member_removed",
        },
        {
            "owner_id": KID,
            "member_id": ELDER,
            "changed_by": ELDER,
            "from_role": None,
            "to_role": None,
            "event": "member_removed",
        },
    ]


@pytest.mark.asyncio
async def test_audit_failure_does_not_turn_a_done_removal_into_an_error():
    repo = FakeTreeRepository(linked_family())

    await make_service(repo, audit=RecordingAudit(fail=True)).remove_member(ELDER, KID)

    assert KID not in ids(repo.trees[ELDER])


@pytest.mark.asyncio
async def test_removing_the_last_unassigned_member_switches_the_owner():
    trees = {
        ELDER: make_tree(
            ELDER,
            [
                FamilyMember(user_id=KID, family_role="GUARDIAN"),
                FamilyMember(user_id=OTHER),
            ],
        ),
        OTHER: make_tree(OTHER, [FamilyMember(user_id=ELDER)]),
    }
    repo = FakeTreeRepository(trees)

    await make_service(repo).remove_member(ELDER, OTHER)

    assert (ELDER, "enforced") in repo.state_writes


# ── 移除成員連帶撤銷委任 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_removal_revokes_delegations_in_both_directions():
    """人都不在族譜裡了，委任沒有存在的理由；留著的話重新受邀時會直接復活成 GUARDIAN。"""
    repo = FakeTreeRepository(linked_family())
    delegations = FakeDelegations(active={(ELDER, KID)})

    await make_service(repo, delegations=delegations).remove_member(ELDER, KID)

    assert (ELDER, KID, ELDER) in delegations.revoked
    assert (KID, ELDER, ELDER) in delegations.revoked
    assert delegations.active == set()


@pytest.mark.asyncio
async def test_removal_by_the_delegate_side_also_revokes():
    """孩子自己退出長輩的家庭，長輩給他的委任一樣要收回。"""
    repo = FakeTreeRepository(linked_family())
    delegations = FakeDelegations(active={(ELDER, KID)})

    await make_service(repo, delegations=delegations).remove_member(KID, ELDER)

    assert delegations.active == set()


@pytest.mark.asyncio
async def test_revoked_delegation_is_audited_only_when_one_existed():
    repo = FakeTreeRepository(linked_family())
    audit = RecordingAudit()
    delegations = FakeDelegations(active={(ELDER, KID)})

    await make_service(repo, audit=audit, delegations=delegations).remove_member(ELDER, KID)

    events = [(e["event"], e["owner_id"], e["member_id"]) for e in audit.entries]
    assert ("delegation_revoked", ELDER, KID) in events
    assert ("delegation_revoked", KID, ELDER) not in events


@pytest.mark.asyncio
async def test_delegation_revocation_failure_does_not_undo_the_removal():
    """撤銷失敗只記 log：is_active_delegate 另外要求成員資格，權限不會因此復活。"""
    repo = FakeTreeRepository(linked_family())

    await make_service(repo, delegations=FakeDelegations(fail=True)).remove_member(ELDER, KID)

    assert KID not in ids(repo.trees[ELDER])
    assert ELDER not in ids(repo.trees[KID])
