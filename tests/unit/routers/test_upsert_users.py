# tests/unit/routers/test_upsert_users.py
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_user_profile_service
from app.main import app


class FakeUserProfileService:
    def __init__(self, result: bool = True, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.upsert_user_profile = AsyncMock(side_effect=self._call)

    async def _call(self, user_id: str, payload: dict) -> bool:
        if self._error is not None:
            raise self._error
        return self._result


@pytest.fixture()
def client():
    return TestClient(app)

#用dependency_overrides，把原本的get_user_profile_service改成
# 用FakeUserProfileService來測試，避免呼叫service
@pytest.fixture()
def override_user_profile_service():
    def _override(service: FakeUserProfileService):
        app.dependency_overrides[get_user_profile_service] = lambda: service
        return service
    
    yield _override
    app.dependency_overrides.clear()

# 下面是測試用的payload，包含所有必填欄位和一些範例資料。
def _valid_payload() -> dict:
    return {
        "name": "Amy",
        "gender": "女性",
        "height": 160.0,
        "weight": 50.0,
        "age": 30,
        "chronic_history": "無",
        "major_illness_history": "無",
        "surgery_history": "無",
        "health_consultations": {"last_visit": "2026-04-20"},
    }

#測試成功的情況，應該回傳200和預期的response body
def test_upsert_user_profile_success_returns_200_and_response_body(
    client, override_user_profile_service
):
    fake_service = override_user_profile_service(FakeUserProfileService(result=True))

    response = client.put("/profiles/U123", json=_valid_payload())
    assert response.status_code == 200
    assert response.json() == {"user_id": "U123", "updated": True}

    fake_service.upsert_user_profile.assert_awaited_once()

#測試payload缺少必填欄位的情況，應該回傳422 Unprocessable Entity
def test_upsert_user_profile_invalid_body_returns_422(
    client, override_user_profile_service
):
    override_user_profile_service(FakeUserProfileService(result=True))

    invalid_payload = _valid_payload()
    invalid_payload.pop("name")

    response = client.put("/profiles/U123", json=invalid_payload)
    assert response.status_code == 422

#測試service層發生錯誤的情況，應該回傳500 Internal Server Error
def test_upsert_user_profile_service_error_returns_500(override_user_profile_service):
    fake_service = override_user_profile_service(
        FakeUserProfileService(error=RuntimeError("db down"))
    )

    error_client = TestClient(app, raise_server_exceptions=False)
    response = error_client.put("/profiles/U123", json=_valid_payload())
    assert response.status_code == 500

    fake_service.upsert_user_profile.assert_awaited_once()

#之後登入後端合併到main後要寫驗證使用者的測試