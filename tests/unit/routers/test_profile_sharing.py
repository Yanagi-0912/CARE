from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient

from app.dependencies import CurrentUser, get_current_user, get_user_profile_service, get_family_tree_service
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
    service = AsyncMock()
    return service

@pytest.fixture()
def override_services(mock_profile_service, mock_family_service):
    app.dependency_overrides[get_user_profile_service] = lambda: mock_profile_service
    app.dependency_overrides[get_family_tree_service] = lambda: mock_family_service
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

    # Mock family tree containing the member
    now = datetime.now(tz=timezone.utc)
    mock_family.get_family_tree.return_value = FamilyTree(
        user_id="U_ME",
        family_members=[FamilyMember(user_id="U_MEMBER", relationship_type="spouse")],
        created_at=now,
        updated_at=now
    )

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
    mock_family.get_family_tree.assert_awaited_once_with("U_ME")
    mock_profile.get_user_profile.assert_awaited_once_with("U_MEMBER")

def test_get_unrelated_member_profile_forbidden(client, override_services, override_current_user):
    override_current_user("U_ME")
    mock_profile, mock_family = override_services

    # Mock family tree NOT containing the member
    now = datetime.now(tz=timezone.utc)
    mock_family.get_family_tree.return_value = FamilyTree(
        user_id="U_ME",
        family_members=[FamilyMember(user_id="U_SOMEONE_ELSE", relationship_type="parent")],
        created_at=now,
        updated_at=now
    )

    response = client.get("/api/profiles/U_STRANGER")
    assert response.status_code == 403
    assert "非家庭成員" in response.json()["detail"]
    mock_family.get_family_tree.assert_awaited_once_with("U_ME")
    mock_profile.get_user_profile.assert_not_awaited()

def test_get_family_member_profile_not_found(client, override_services, override_current_user):
    override_current_user("U_ME")
    mock_profile, mock_family = override_services

    now = datetime.now(tz=timezone.utc)
    mock_family.get_family_tree.return_value = FamilyTree(
        user_id="U_ME",
        family_members=[FamilyMember(user_id="U_MEMBER", relationship_type="child")],
        created_at=now,
        updated_at=now
    )

    mock_profile.get_user_profile.return_value = None

    response = client.get("/api/profiles/U_MEMBER")
    assert response.status_code == 404
    assert "找不到該成員" in response.json()["detail"]
