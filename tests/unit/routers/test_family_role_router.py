"""角色管理與委任端點的授權邊界。

這裡驗的是「後端才是真正的安全邊界」：每一條規則都用 HTTP 請求直接打，不經過
任何前端。前端能不能渲染那個按鈕與這些結果無關——使用者略過介面直接呼叫時，
得到的必須是同一組答案。

依賴以 `app.dependency_overrides` 替換（FastAPI 內建的注入點，非 monkey patch）。
服務層以真實的 `FamilyRoleService` 搭配假 repository 組裝，這樣測到的是端點
到服務的整條路徑，而不只是端點有沒有把參數傳下去。
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_family_delegation_service,
    get_family_role_service,
)
from app.main import app
from app.models.family_tree import FamilyMember, FamilyTree
from app.services.family.family_delegation_service import FamilyDelegationService
from app.services.family.family_role_service import FamilyRoleService

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
OWNER = "U-owner"
OPERATOR = "U-operator"
MEMBER = "U-member"


class FakeTreeRepo:
    def __init__(self, trees=None):
        self.trees = trees or {}
        self.writes = []

    async def get_by_user_id(self, user_id):
        return self.trees.get(user_id)

    async def set_family_role(self, owner_id, member_id, family_role):
        self.writes.append((owner_id, member_id, family_role))
        tree = self.trees.get(owner_id)
        for m in tree.family_members:
            if m.user_id == member_id:
                m.family_role = family_role
                return tree
        return None


class FakeAuthz:
    def __init__(self, delegates=None, status=None):
        self.delegates = delegates or set()
        self.status = status

    async def is_active_delegate(self, operator_id, owner_id):
        return (operator_id, owner_id) in self.delegates

    async def role_assignment_status(self, owner_id):
        return self.status


class FakeAudit:
    def __init__(self):
        self.entries = []

    async def append(self, **kwargs):
        self.entries.append(kwargs)


class FakeDelegationRepo:
    def __init__(self):
        self.revoked = []

    async def revoke(self, owner_id, delegate_user_id, revoked_by):
        self.revoked.append((owner_id, delegate_user_id, revoked_by))
        return 1

    async def grant(self, **kwargs):  # pragma: no cover - 閘門關閉時不該被呼叫
        raise AssertionError("啟用閘門關閉時不應呼叫 grant")

    async def list_active(self, owner_id):
        return []


def tree_with(member_role=None, owner=OWNER):
    return FamilyTree(
        user_id=owner,
        family_members=[FamilyMember(user_id=MEMBER, family_role=member_role)],
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def wire():
    """組裝真實服務 + 假 repository，並覆寫身分。"""
    state = {}

    def _wire(caller: str, trees=None, delegates=None, activation_enabled=False):
        repo = FakeTreeRepo(trees if trees is not None else {OWNER: tree_with()})
        audit = FakeAudit()
        delegation_repo = FakeDelegationRepo()
        role_service = FamilyRoleService(
            authorization_service=FakeAuthz(delegates),
            family_tree_repository=repo,
            audit_repository=audit,
        )
        delegation_service = FamilyDelegationService(
            delegation_repository=delegation_repo,
            family_tree_repository=repo,
            audit_repository=audit,
            activation_enabled=activation_enabled,
        )
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            line_user_id=caller
        )
        app.dependency_overrides[get_family_role_service] = lambda: role_service
        app.dependency_overrides[get_family_delegation_service] = (
            lambda: delegation_service
        )
        state.update(repo=repo, audit=audit, delegation_repo=delegation_repo)
        return state

    yield _wire
    app.dependency_overrides.clear()


# ── 擁有者管理自己的家庭 ────────────────────────────────────────────


def test_owner_assigns_role_in_own_circle(client, wire):
    state = wire(OWNER)
    res = client.put(f"/api/family/members/{MEMBER}/role", json={"family_role": "GUARDIAN"})
    assert res.status_code == 200
    assert state["repo"].writes == [(OWNER, MEMBER, "GUARDIAN")]


def test_assigning_owner_returns_400_not_422(client, wire):
    """spec 明訂回 400。若請求模型用嚴格型別，這裡會變成 422。"""
    state = wire(OWNER)
    res = client.put(f"/api/family/members/{MEMBER}/role", json={"family_role": "OWNER"})
    assert res.status_code == 400
    assert state["repo"].writes == []


def test_assigning_role_to_the_owner_themselves_returns_400(client, wire):
    state = wire(OWNER)
    res = client.put(f"/api/family/members/{OWNER}/role", json={"family_role": "MEMBER"})
    assert res.status_code == 400
    assert state["repo"].writes == []


def test_member_not_in_tree_returns_404(client, wire):
    state = wire(OWNER)
    res = client.put("/api/family/members/U-nobody/role", json={"family_role": "MEMBER"})
    assert res.status_code == 404
    assert state["repo"].writes == []


# ── 跨擁有者：必須先過委任判定 ─────────────────────────────────────


def test_non_delegate_cannot_manage_another_owners_circle(client, wire):
    """帶得出 ownerId 不構成任何允許的依據。"""
    state = wire(OPERATOR)
    res = client.put(
        f"/api/family/owners/{OWNER}/members/{MEMBER}/role",
        json={"family_role": "GUARDIAN"},
    )
    assert res.status_code == 403
    assert state["repo"].writes == []


def test_guardian_by_assignment_still_cannot_manage_roles(client, wire):
    """資料權限與代為行事是兩件事，不得互相推導。"""
    state = wire(
        OPERATOR,
        trees={
            OWNER: FamilyTree(
                user_id=OWNER,
                family_members=[
                    FamilyMember(user_id=OPERATOR, family_role="GUARDIAN"),
                    FamilyMember(user_id=MEMBER, family_role="MEMBER"),
                ],
                created_at=NOW,
                updated_at=NOW,
            )
        },
    )
    res = client.put(
        f"/api/family/owners/{OWNER}/members/{MEMBER}/role",
        json={"family_role": "CAREGIVER"},
    )
    assert res.status_code == 403
    assert state["repo"].writes == []


def test_delegate_may_assign_caregiver(client, wire):
    state = wire(OPERATOR, delegates={(OPERATOR, OWNER)})
    res = client.put(
        f"/api/family/owners/{OWNER}/members/{MEMBER}/role",
        json={"family_role": "CAREGIVER"},
    )
    assert res.status_code == 200
    assert state["repo"].writes == [(OWNER, MEMBER, "CAREGIVER")]
    assert state["audit"].entries[0]["via_delegation"] is True


def test_delegate_cannot_assign_guardian(client, wire):
    """避免委任鏈：受委任者造一個 GUARDIAN，那個人再造下一個。"""
    state = wire(OPERATOR, delegates={(OPERATOR, OWNER)})
    res = client.put(
        f"/api/family/owners/{OWNER}/members/{MEMBER}/role",
        json={"family_role": "GUARDIAN"},
    )
    assert res.status_code == 403
    assert state["repo"].writes == []


def test_delegation_does_not_cross_families(client, wire):
    other = "U-other-owner"
    state = wire(
        OPERATOR,
        trees={OWNER: tree_with(), other: tree_with(owner=other)},
        delegates={(OPERATOR, OWNER)},
    )
    ok = client.put(
        f"/api/family/owners/{OWNER}/members/{MEMBER}/role",
        json={"family_role": "MEMBER"},
    )
    denied = client.put(
        f"/api/family/owners/{other}/members/{MEMBER}/role",
        json={"family_role": "MEMBER"},
    )
    assert ok.status_code == 200
    assert denied.status_code == 403


# ── 角色清單與指派狀態 ───────────────────────────────────────────────


def test_role_list_is_not_open_to_ordinary_members(client, wire):
    """「誰有什麼權限」本身就是管理資訊。"""
    wire(OPERATOR)
    assert client.get(f"/api/family/owners/{OWNER}/members/roles").status_code == 403


def test_role_list_distinguishes_unset_from_member(client, wire):
    wire(OWNER, trees={OWNER: tree_with(None)})
    res = client.get("/api/family/members/roles")
    assert res.status_code == 200
    entry = res.json()[0]
    assert entry["family_role"] is None
    assert entry["effective_family_role"] == "MEMBER"


def test_assignment_status_reports_unassigned_members(client, wire):
    from app.models.family_tree import FamilyRoleAssignmentStatus

    state = wire(OWNER)
    app.dependency_overrides[get_family_role_service] = lambda: FamilyRoleService(
        authorization_service=FakeAuthz(
            status=FamilyRoleAssignmentStatus(
                owner_id=OWNER,
                is_complete=False,
                unassigned_member_ids=[MEMBER],
                rbac_migration_state="shadow",
            )
        ),
        family_tree_repository=state["repo"],
        audit_repository=state["audit"],
    )
    res = client.get("/api/family/role-assignment-status")
    assert res.status_code == 200
    assert res.json()["is_complete"] is False
    assert res.json()["unassigned_member_ids"] == [MEMBER]


# ── 委任端點 ──────────────────────────────────────────────────────────


def test_owner_can_revoke_delegation_even_while_activation_is_closed(client, wire):
    """閘門管的是能不能給出去，不是能不能收回來。"""
    state = wire(OWNER, activation_enabled=False)
    res = client.delete("/api/family/delegations/U-delegate")
    assert res.status_code == 200
    assert res.json() == {"revoked": 1}
    assert state["delegation_repo"].revoked == [(OWNER, "U-delegate", OWNER)]


def test_non_owner_cannot_revoke_delegation(client, wire):
    state = wire(OPERATOR, activation_enabled=False)
    # 呼叫者是 OPERATOR，端點恆以呼叫者為 owner，因此撤銷的是他自己的委任；
    # 這裡驗的是它不會變成「代別人撤銷」的入口——路徑上沒有 ownerId。
    res = client.delete("/api/family/delegations/U-delegate")
    assert res.status_code == 200
    assert state["delegation_repo"].revoked == [(OPERATOR, "U-delegate", OPERATOR)]


def test_there_is_no_grant_endpoint_exposed():
    """核可流程確定前，建立委任的路徑不對終端使用者開放。

    這條測試釘住「沒有這個端點」本身——委任的資料模型與權限邊界都寫好了，
    很容易讓人以為問題已經解決，實際上開放與否取決於一份還不存在的流程。
    """
    paths = {getattr(r, "path", "") for r in app.routes}
    grant_paths = [
        p for p in paths if "delegation" in p.lower() and "grant" in p.lower()
    ]
    assert grant_paths == []
