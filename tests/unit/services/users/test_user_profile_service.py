from unittest.mock import AsyncMock

import pytest

from app.models.user import UserSettingsUpdate
from app.services.users.user_profile_service import UserProfileService


def _build_service(get_user_profile_return=None, update_user_settings_return=True):
    repo = AsyncMock()
    repo.get_user_profile.return_value = get_user_profile_return
    repo.update_user_settings.return_value = update_user_settings_return
    return UserProfileService(repo=repo), repo


@pytest.mark.asyncio
async def test_get_user_settings_returns_defaults_when_missing_in_db():
    service, repo = _build_service(get_user_profile_return={"line_id": "U123"})

    settings = await service.get_user_settings("U123")

    repo.get_user_profile.assert_awaited_once_with("U123")
    assert settings == {
        "language": None,
        "font_size": "large",
        "high_contrast": True,
        "notify_reminder": True,
        "notify_family": True,
        "voice_reply_enabled": False,
    }


@pytest.mark.asyncio
async def test_get_user_settings_returns_defaults_when_profile_not_found():
    service, _ = _build_service(get_user_profile_return=None)

    settings = await service.get_user_settings("U123")

    assert settings["font_size"] == "large"


@pytest.mark.asyncio
async def test_get_user_settings_merges_stored_values_with_defaults():
    service, _ = _build_service(
        get_user_profile_return={
            "line_id": "U123",
            "settings": {"font_size": "xlarge", "high_contrast": False},
        }
    )

    settings = await service.get_user_settings("U123")

    assert settings["font_size"] == "xlarge"
    assert settings["high_contrast"] is False
    # 沒存過的欄位仍要回預設值
    assert settings["notify_reminder"] is True


@pytest.mark.asyncio
async def test_update_user_settings_only_writes_provided_fields():
    stored_profile = {
        "line_id": "U123",
        "settings": {"font_size": "xlarge"},
    }
    service, repo = _build_service()
    repo.get_user_profile.side_effect = lambda _line_id: stored_profile

    async def _fake_update_user_settings(_line_id, changed_fields):
        stored_profile["settings"].update(changed_fields)
        return True

    repo.update_user_settings.side_effect = _fake_update_user_settings

    result = await service.update_user_settings(
        "U123", UserSettingsUpdate(high_contrast=False)
    )

    repo.update_user_settings.assert_awaited_once_with("U123", {"high_contrast": False})
    assert result["font_size"] == "xlarge"
    assert result["high_contrast"] is False


@pytest.mark.asyncio
async def test_update_user_settings_skips_repo_call_when_nothing_changed():
    service, repo = _build_service(get_user_profile_return={"line_id": "U123"})

    await service.update_user_settings("U123", UserSettingsUpdate())

    repo.update_user_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_default_user_profile_includes_default_settings():
    service, repo = _build_service()
    repo.upsert_user_profile.return_value = True

    ok = await service.create_default_user_profile(
        line_id="U123",
        display_name="Amy",
        picture_url="https://line.example/pic.jpg",
        language="zh-TW",
    )

    assert ok is True
    repo.upsert_user_profile.assert_awaited_once()
    _, payload = repo.upsert_user_profile.await_args.args
    assert payload["settings"] == {
        "language": "zh-TW",
        "font_size": "large",
        "high_contrast": True,
        "notify_reminder": True,
        "notify_family": True,
        "voice_reply_enabled": False,
    }


@pytest.mark.asyncio
async def test_update_voice_reply_enabled_calls_repo_once():
    service, repo = _build_service()
    repo.update_voice_reply_enabled.return_value = True

    ok = await service.update_voice_reply_enabled("U123", True)

    assert ok is True
    repo.update_voice_reply_enabled.assert_awaited_once_with("U123", True)
    repo.update_user_settings.assert_not_awaited()

