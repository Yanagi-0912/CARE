from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_user_profile_service,
    get_family_authorization_service,
)
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)
from app.main import app
from app.models.family_tree import FamilyTree, FamilyMember
from datetime import datetime, timezone

@pytest.fixture()
def client():
    return TestClient(app)

@pytest.fixture()
def mock_profile_service():
    service = AsyncMock()
    return service

@pytest.fixture()
def mock_family_service():
    """真的 FamilyAuthorizationService，只把 repository 換成可設定的假物件。

    授權判定看的是**目標擁有者**的族譜（他的族譜裡有沒有呼叫者），方向與
    本 change 之前相反，因此 `set_family` 以「這些人是呼叫者的家人」的說法
    為每一位各造一份文件。
    """

    class _Trees:
        def __init__(self):
            self.trees = {}

        async def get_by_user_id(self, user_id):
            return self.trees.get(user_id)

    class _Delegations:
        async def has_active_delegation(self, owner_id, delegate_user_id, now=None):
            return False

    repo = _Trees()
    service = FamilyAuthorizationService(
        family_tree_repository=repo,
        delegation_repository=_Delegations(),
        enforcement_enabled=False,
    )

    def set_family(member_ids, caller_id="U_ME", state="shadow", roles=None):
        now = datetime.now(timezone.utc)
        roles = roles or {}
        repo.trees = {
            member: FamilyTree(
                user_id=member,
                family_members=[
                    FamilyMember(user_id=caller_id, family_role=roles.get(member))
                ],
                rbac_migration_state=state,
                created_at=now,
                updated_at=now,
            )
            for member in member_ids
        }

    service.set_family = set_family
    return service

@pytest.fixture()
def override_services(mock_profile_service, mock_family_service):
    app.dependency_overrides[get_user_profile_service] = lambda: mock_profile_service
    app.dependency_overrides[get_family_authorization_service] = lambda: mock_family_service
    yield mock_profile_service, mock_family_service
    app.dependency_overrides.clear()

@pytest.fixture()
def override_current_user():
    def _override(user_id: str = "U_ME"):
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            line_user_id=user_id
        )
    yield _override
    app.dependency_overrides.clear()

def test_get_own_profile_success(client, override_services, override_current_user):
    override_current_user("U_ME")
    mock_profile, _ = override_services
    mock_profile.get_user_profile.return_value = {
        "name": "My Name",
        "gender": "男",
        "height": 180.0,
        "weight": 75.0,
        "age": 30,
        "chronic_history": "無",
        "major_illness_history": "無",
        "surgery_history": "無",
        "health_consultations": {}
    }

    response = client.get("/api/profiles/U_ME")
    assert response.status_code == 200
    assert response.json()["name"] == "My Name"
    mock_profile.get_user_profile.assert_awaited_once_with("U_ME")

def test_get_family_member_profile_success(client, override_services, override_current_user):
    override_current_user("U_ME")
    mock_profile, mock_family = override_services

    mock_family.set_family(["U_MEMBER"])

    mock_profile.get_user_profile.return_value = {
        "name": "Member Name",
        "gender": "女",
        "height": 160.0,
        "weight": 50.0,
        "age": 28,
        "chronic_history": "無",
        "major_illness_history": "無",
        "surgery_history": "無",
        "health_consultations": {}
    }

    response = client.get("/api/profiles/U_MEMBER")
    assert response.status_code == 200
    assert response.json()["name"] == "Member Name"
    mock_profile.get_user_profile.assert_awaited_once_with("U_MEMBER")

def test_get_unrelated_member_profile_forbidden(client, override_services, override_current_user):
    override_current_user("U_ME")
    mock_profile, mock_family = override_services

    mock_family.set_family(["U_SOMEONE_ELSE"])

    response = client.get("/api/profiles/U_STRANGER")
    assert response.status_code == 403
    assert "權限不足" in response.json()["detail"]
    # 授權擋在讀取之前，不是先撈出來再決定要不要回傳
    mock_profile.get_user_profile.assert_not_awaited()

def test_get_family_member_profile_not_found(client, override_services, override_current_user):
    override_current_user("U_ME")
    mock_profile, mock_family = override_services

    mock_family.set_family(["U_MEMBER"])

    mock_profile.get_user_profile.return_value = None

    response = client.get("/api/profiles/U_MEMBER")
    assert response.status_code == 404
    assert "找不到該成員" in response.json()["detail"]
