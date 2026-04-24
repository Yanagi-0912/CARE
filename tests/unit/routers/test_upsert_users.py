# 測試更新、插入使用者資料API的測試程式
# pytest tests/unit/routers/test_upsert_users.py
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.dependencies import get_user_profile_service
from app.main import app


# 測試用的假物件，模擬 ProfileService 的行為
# Stub 在測試裡通常表示「固定回應的替身」，我用fake
class FakeProfileService:
    def __init__(self, result: bool = True, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.upsert_user_profile = AsyncMock(side_effect=self._call)

    # 當測試真的呼叫 upsert_user_profile 時，跑這段邏輯
    async def _call(self, user_id: str, payload: dict) -> bool:
        if self._error is not None:
            raise self._error
        return self._result


client = TestClient(app)


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


# 測試成功更新或插入使用者資料，預期回 200 和正確的 response body
def test_upsert_user_profile_success_returns_200_and_response_body():
    # 用 dependency override 注入假的 service，隔離 router 行為
    fake_service = FakeProfileService(result=True)
    app.dependency_overrides[get_user_profile_service] = lambda: fake_service

    response = client.put("/profiles/U123", json=_valid_payload())

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"user_id": "U123", "updated": True}
    fake_service.upsert_user_profile.assert_awaited_once()


# 缺少必要欄位時，應由 Pydantic/FastAPI 回 422
def test_upsert_user_profile_invalid_body_returns_422():

    fake_service = FakeProfileService(result=True)
    app.dependency_overrides[get_user_profile_service] = lambda: fake_service

    invalid_payload = _valid_payload()
    # pop是用來移除欄位的
    invalid_payload.pop("name")

    response = client.put("/profiles/U123", json=invalid_payload)

    app.dependency_overrides.clear()

    assert response.status_code == 422


def test_upsert_user_profile_service_error_returns_500():
    # service 丟例外時，router 目前未攔截，預期回 500
    fake_service = FakeProfileService(error=RuntimeError("db down"))
    app.dependency_overrides[get_user_profile_service] = lambda: fake_service

    error_client = TestClient(app, raise_server_exceptions=False)
    response = error_client.put("/profiles/U123", json=_valid_payload())

    app.dependency_overrides.clear()

    assert response.status_code == 500
