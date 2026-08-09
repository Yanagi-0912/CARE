# tests/unit/routers/test_upsert_users.py
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.dependencies import CurrentUser, get_current_user, get_user_profile_service
from app.main import app


DEFAULT_SETTINGS = {
    "language": None,
    "font_size": "large",
    "high_contrast": True,
    "notify_reminder": True,
    "notify_family": True,
    "voice_reply_enabled": False,
    "voice_rate": "normal",
    "voice_gender": "female",
}


class FakeUserProfileService:
    def __init__(
        self,
        result: bool = True,
        error: Exception | None = None,
        settings: dict | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self._settings = settings or dict(DEFAULT_SETTINGS)
        self.upsert_user_profile = AsyncMock(side_effect=self._call)
        self.get_user_settings = AsyncMock(side_effect=self._get_settings)
        self.update_user_settings = AsyncMock(side_effect=self._update_settings)

    async def _call(self, user_id: str, payload: dict) -> bool:
        if self._error is not None:
            raise self._error
        return self._result

    async def _get_settings(self, user_id: str) -> dict:
        if self._error is not None:
            raise self._error
        return self._settings

    async def _update_settings(self, user_id: str, update) -> dict:
        if self._error is not None:
            raise self._error
        changed = update.model_dump(exclude_unset=True, exclude_none=True)
        self._settings = {**self._settings, **changed}
        return self._settings


@pytest.fixture()
def client():
    return TestClient(app)


# 用dependency_overrides，把原本的get_user_profile_service改成
# 用FakeUserProfileService來測試，避免呼叫service
@pytest.fixture()
def override_user_profile_service():
    def _override(service: FakeUserProfileService):
        app.dependency_overrides[get_user_profile_service] = lambda: service
        return service

    yield _override
    app.dependency_overrides.clear()


@pytest.fixture()
def override_current_user():
    def _override(user_id: str = "U123"):
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            line_user_id=user_id
        )

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
    }


# 測試成功的情況，應該回傳200和預期的response body
def test_upsert_user_profile_success_returns_200_and_response_body(
    client, override_user_profile_service, override_current_user
):
    override_current_user("U123")
    fake_service = override_user_profile_service(FakeUserProfileService(result=True))

    response = client.put("/api/profiles/me/update", json=_valid_payload())
    assert response.status_code == 200
    assert response.json() == {"user_id": "U123", "updated": True}

    fake_service.upsert_user_profile.assert_awaited_once()


# 測試payload缺少必填欄位的情況，應該回傳422 Unprocessable Entity
def test_upsert_user_profile_invalid_body_returns_422(
    client, override_user_profile_service, override_current_user
):
    override_current_user("U123")
    override_user_profile_service(FakeUserProfileService(result=True))

    invalid_payload = _valid_payload()
    invalid_payload.pop("name")

    response = client.put("/api/profiles/me/update", json=invalid_payload)
    assert response.status_code == 422


# 測試service層發生錯誤的情況，應該回傳500 Internal Server Error
def test_upsert_user_profile_service_error_returns_500(override_user_profile_service):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        line_user_id="U123"
    )
    fake_service = override_user_profile_service(
        FakeUserProfileService(error=RuntimeError("db down"))
    )

    error_client = TestClient(app, raise_server_exceptions=False)
    response = error_client.put("/api/profiles/me/update", json=_valid_payload())
    assert response.status_code == 500

    fake_service.upsert_user_profile.assert_awaited_once()


# 之後登入後端合併到main後要寫驗證使用者的測試


def test_get_user_settings_returns_200_and_defaults(
    client, override_user_profile_service, override_current_user
):
    override_current_user("U123")
    override_user_profile_service(FakeUserProfileService())

    response = client.get("/api/profiles/me/settings")
    assert response.status_code == 200
    assert response.json() == {"user_id": "U123", "settings": DEFAULT_SETTINGS}


def test_patch_user_settings_only_updates_provided_fields(
    client, override_user_profile_service, override_current_user
):
    override_current_user("U123")
    fake_service = override_user_profile_service(FakeUserProfileService())

    response = client.patch(
        "/api/profiles/me/settings", json={"font_size": "xlarge"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["settings"]["font_size"] == "xlarge"
    assert body["settings"]["high_contrast"] is True

    fake_service.update_user_settings.assert_awaited_once()


def test_patch_user_settings_invalid_font_size_returns_422(
    client, override_user_profile_service, override_current_user
):
    override_current_user("U123")
    override_user_profile_service(FakeUserProfileService())

    response = client.patch(
        "/api/profiles/me/settings", json={"font_size": "huge"}
    )
    assert response.status_code == 422


def test_patch_user_settings_voice_rate_slow_updates_value(
    client, override_user_profile_service, override_current_user
):
    override_current_user("U123")
    fake_service = override_user_profile_service(FakeUserProfileService())

    response = client.patch(
        "/api/profiles/me/settings", json={"voice_rate": "slow"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["settings"]["voice_rate"] == "slow"

    fake_service.update_user_settings.assert_awaited_once()


def test_patch_user_settings_invalid_voice_rate_returns_422_and_keeps_existing_value(
    client, override_user_profile_service, override_current_user
):
    override_current_user("U123")
    fake_service = override_user_profile_service(
        FakeUserProfileService(settings={**DEFAULT_SETTINGS, "voice_rate": "fast"})
    )

    response = client.patch(
        "/api/profiles/me/settings", json={"voice_rate": "super_fast"}
    )
    assert response.status_code == 422

    fake_service.update_user_settings.assert_not_awaited()
    assert fake_service._settings["voice_rate"] == "fast"


def test_patch_user_settings_other_field_does_not_change_voice_rate(
    client, override_user_profile_service, override_current_user
):
    override_current_user("U123")
    fake_service = override_user_profile_service(
        FakeUserProfileService(settings={**DEFAULT_SETTINGS, "voice_rate": "fast"})
    )

    response = client.patch(
        "/api/profiles/me/settings", json={"font_size": "xlarge"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["settings"]["font_size"] == "xlarge"
    assert body["settings"]["voice_rate"] == "fast"

    fake_service.update_user_settings.assert_awaited_once()


def test_patch_user_settings_voice_gender_male_updates_value(
    client, override_user_profile_service, override_current_user
):
    override_current_user("U123")
    fake_service = override_user_profile_service(FakeUserProfileService())

    response = client.patch(
        "/api/profiles/me/settings", json={"voice_gender": "male"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["settings"]["voice_gender"] == "male"

    fake_service.update_user_settings.assert_awaited_once()


def test_patch_user_settings_invalid_voice_gender_returns_422_and_keeps_existing_value(
    client, override_user_profile_service, override_current_user
):
    override_current_user("U123")
    fake_service = override_user_profile_service(
        FakeUserProfileService(settings={**DEFAULT_SETTINGS, "voice_gender": "male"})
    )

    response = client.patch(
        "/api/profiles/me/settings", json={"voice_gender": "robot"}
    )
    assert response.status_code == 422

    fake_service.update_user_settings.assert_not_awaited()
    assert fake_service._settings["voice_gender"] == "male"


def test_patch_user_settings_other_field_does_not_change_voice_gender(
    client, override_user_profile_service, override_current_user
):
    override_current_user("U123")
    fake_service = override_user_profile_service(
        FakeUserProfileService(settings={**DEFAULT_SETTINGS, "voice_gender": "male"})
    )

    response = client.patch(
        "/api/profiles/me/settings", json={"font_size": "xlarge"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["settings"]["font_size"] == "xlarge"
    assert body["settings"]["voice_gender"] == "male"

    fake_service.update_user_settings.assert_awaited_once()
