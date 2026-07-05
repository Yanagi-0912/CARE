from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.liff.auth_service import DEFAULT_LANGUAGE, LiffAuthApplicationService


def _build_service(
    *,
    verify_payload: dict,
    existing_profile: dict | None,
    line_api_language: str | None = None,
):
    line_id_token_service = MagicMock()
    line_id_token_service.verify.return_value = verify_payload

    jwt_service = MagicMock()
    jwt_service.issue_for_user.return_value = ("app-jwt", 3600)

    user_profile_service = MagicMock()
    user_profile_service.get_user_profile = AsyncMock(return_value=existing_profile)
    user_profile_service.create_default_user_profile = AsyncMock(return_value=True)
    user_profile_service.sync_line_profile = AsyncMock(return_value=True)

    line_language_service = MagicMock()
    line_language_service.get_language.return_value = line_api_language

    service = LiffAuthApplicationService(
        line_id_token_service=line_id_token_service,
        jwt_service=jwt_service,
        user_profile_service=user_profile_service,
        line_language_service=line_language_service,
    )
    return service, user_profile_service, line_language_service


@pytest.mark.asyncio
async def test_new_user_uses_line_language_as_initial_default(monkeypatch):
    monkeypatch.setattr(
        "app.services.liff.auth_service.settings.LIFF_CHANNEL_ID",
        "liff-client-id",
    )
    monkeypatch.setattr(
        "app.services.liff.auth_service.settings.LINE_CHANNEL_ID",
        "line-client-id",
    )

    service, user_profile_service, line_language_service = _build_service(
        verify_payload={"sub": "U123", "name": "Amy", "picture": "https://id-token.example/pic.jpg"},
        existing_profile=None,
        line_api_language="ja",
    )

    result = await service.login_with_id_token("id-token")

    assert result["access_token"] == "app-jwt"
    assert result["line_user_id"] == "U123"
    assert result["language"] == "ja"
    line_language_service.get_language.assert_called_once_with("U123")
    user_profile_service.create_default_user_profile.assert_awaited_once_with(
        line_id="U123",
        display_name="Amy",
        picture_url="https://id-token.example/pic.jpg",
        language="ja",
    )
    user_profile_service.sync_line_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_user_falls_back_to_default_language_when_line_api_unavailable(monkeypatch):
    monkeypatch.setattr(
        "app.services.liff.auth_service.settings.LIFF_CHANNEL_ID",
        "liff-client-id",
    )
    monkeypatch.setattr(
        "app.services.liff.auth_service.settings.LINE_CHANNEL_ID",
        "line-client-id",
    )

    service, user_profile_service, _line_language_service = _build_service(
        verify_payload={"sub": "U123", "name": "Amy", "picture": "https://id-token.example/pic.jpg"},
        existing_profile=None,
        line_api_language=None,
    )

    result = await service.login_with_id_token("id-token")

    assert result["language"] == DEFAULT_LANGUAGE
    user_profile_service.create_default_user_profile.assert_awaited_once_with(
        line_id="U123",
        display_name="Amy",
        picture_url="https://id-token.example/pic.jpg",
        language=DEFAULT_LANGUAGE,
    )


@pytest.mark.asyncio
async def test_existing_user_uses_db_language_and_skips_line_api(monkeypatch):
    monkeypatch.setattr(
        "app.services.liff.auth_service.settings.LIFF_CHANNEL_ID",
        "liff-client-id",
    )
    monkeypatch.setattr(
        "app.services.liff.auth_service.settings.LINE_CHANNEL_ID",
        "line-client-id",
    )

    service, user_profile_service, line_language_service = _build_service(
        verify_payload={"sub": "U999", "name": "Bob", "picture": "https://id-token.example/bob.jpg"},
        existing_profile={"line_id": "U999", "name": "Bob", "language": "en"},
        line_api_language="zh-TW",  # 假設 LINE 帳號現在是中文，但不應該被使用
    )

    result = await service.login_with_id_token("id-token")

    assert result["line_user_id"] == "U999"
    assert result["language"] == "en"  # 以 DB 為準，不是 LINE 當下的語言
    line_language_service.get_language.assert_not_called()
    user_profile_service.create_default_user_profile.assert_not_awaited()
    user_profile_service.sync_line_profile.assert_awaited_once_with(
        line_id="U999",
        picture_url="https://id-token.example/bob.jpg",
    )


@pytest.mark.asyncio
async def test_existing_user_without_language_field_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(
        "app.services.liff.auth_service.settings.LIFF_CHANNEL_ID",
        "liff-client-id",
    )
    monkeypatch.setattr(
        "app.services.liff.auth_service.settings.LINE_CHANNEL_ID",
        "line-client-id",
    )

    service, user_profile_service, line_language_service = _build_service(
        verify_payload={"sub": "U999", "name": "Bob", "picture": "https://id-token.example/bob.jpg"},
        existing_profile={"line_id": "U999", "name": "Bob"},  # 舊資料沒有 language 欄位
    )

    result = await service.login_with_id_token("id-token")

    assert result["language"] == DEFAULT_LANGUAGE
    line_language_service.get_language.assert_not_called()
