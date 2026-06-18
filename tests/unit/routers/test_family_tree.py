from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient

from app.dependencies import CurrentUser, get_current_user, get_family_tree_service
from app.main import app
from app.models.family_tree import CreateInviteResponse, VerifyInviteResponse, AcceptInviteResponse

@pytest.fixture()
def client():
    return TestClient(app)

@pytest.fixture()
def mock_family_service():
    service = AsyncMock()
    return service

@pytest.fixture()
def override_family_service(mock_family_service):
    app.dependency_overrides[get_family_tree_service] = lambda: mock_family_service
    yield mock_family_service
    app.dependency_overrides.clear()

@pytest.fixture()
def override_current_user():
    def _override(user_id: str = "U123"):
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            line_user_id=user_id
        )
    yield _override
    app.dependency_overrides.clear()

def test_create_invite_success(client, override_family_service, override_current_user):
    override_current_user("U_ME")
    override_family_service.create_invitation.return_value = CreateInviteResponse(
        invite_token="token123",
        expires_at="2026-05-20T00:00:00Z"
    )

    response = client.post("/api/family/invites")
    assert response.status_code == 200
    assert response.json()["invite_token"] == "token123"
    override_family_service.create_invitation.assert_awaited_once_with("U_ME")

def test_verify_invite_public_access(client, override_family_service):
    # Verify 應該是公開的，不需要 override_current_user
    override_family_service.verify_invitation.return_value = VerifyInviteResponse(
        inviter_display_name="小明",
        expires_at="2026-05-20T00:00:00Z"
    )

    response = client.get("/api/family/invites/verify/token123")
    assert response.status_code == 200
    assert response.json()["inviter_display_name"] == "小明"
    override_family_service.verify_invitation.assert_awaited_once_with("token123")

def test_accept_invite_success(client, override_family_service, override_current_user):
    override_current_user("U_ME")
    override_family_service.accept_invitation.return_value = AcceptInviteResponse(
        status="joined"
    )

    response = client.post("/api/family/invites/accept", json={"code": "token123"})
    assert response.status_code == 200
    assert response.json()["status"] == "joined"
    override_family_service.accept_invitation.assert_awaited_once_with(invitee_id="U_ME", code="token123")

def test_create_invite_unauthorized(client, override_family_service):
    # 沒有提供 token (沒有 override_current_user) 應該回傳 401
    # 註：這裡假設 get_current_user 會拋出 401，符合 app/dependencies.py 的實作
    response = client.post("/api/family/invites")
    assert response.status_code == 401


def test_get_my_tree_success(
    client, override_family_service, override_current_user
):
    from datetime import datetime, timezone

    from app.models.family_tree import FamilyTree

    override_current_user("U_ME")
    mock_tree = FamilyTree(
        user_id="U_ME",
        family_members=[],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    override_family_service.get_family_tree.return_value = mock_tree

    response = client.get("/api/family/me")
    assert response.status_code == 200
    assert response.json()["family_tree"]["user_id"] == "U_ME"
    override_family_service.get_family_tree.assert_awaited_once_with("U_ME")
