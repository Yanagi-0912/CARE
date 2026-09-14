from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.dependencies import CurrentUser, get_current_user, get_family_tree_service
from app.main import app


@pytest.fixture()
def service():
    mock = AsyncMock()
    app.dependency_overrides[get_family_tree_service] = lambda: mock
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(line_user_id="U_ME")
    yield mock
    app.dependency_overrides.clear()


def test_delete_member_removes_the_link_for_the_current_user(service):
    response = TestClient(app).delete("/api/family/members/U_KID")

    assert response.status_code == 200
    assert response.json() == {"removed": True}
    # 操作者一律取自登入身分，路徑只帶得出「對方是誰」。
    service.remove_member.assert_awaited_once_with(operator_id="U_ME", member_id="U_KID")


def test_delete_member_passes_through_not_found(service):
    service.remove_member.side_effect = HTTPException(status_code=404, detail="找不到")

    response = TestClient(app).delete("/api/family/members/U_STRANGER")

    assert response.status_code == 404
