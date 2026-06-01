from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.liff.auth_service import LiffAuthApplicationService


@pytest.mark.asyncio
async def test_login_with_id_token_creates_default_profile_when_user_not_found(monkeypatch):
    monkeypatch.setattr(
        "app.services.liff.auth_service.settings.LIFF_CHANNEL_ID",
        "liff-client-id",
    )
    monkeypatch.setattr(
        "app.services.liff.auth_service.settings.LINE_CHANNEL_ID",
        "line-client-id",
    )

    line_id_token_service = MagicMock()
    line_id_token_service.verify.return_value = {"sub": "U123", "name": "Amy"}

    jwt_service = MagicMock()
    jwt_service.issue_for_user.return_value = ("app-jwt", 3600)

    user_profile_service = MagicMock()
    user_profile_service.get_user_profile = AsyncMock(return_value=None)
    user_profile_service.create_default_user_profile = AsyncMock(return_value=True)

    service = LiffAuthApplicationService(
        line_id_token_service=line_id_token_service,
        jwt_service=jwt_service,
        user_profile_service=user_profile_service,
    )

    result = await service.login_with_id_token("id-token")

    assert result["access_token"] == "app-jwt"
    assert result["expires_in"] == 3600
    assert result["line_user_id"] == "U123"
    user_profile_service.get_user_profile.assert_awaited_once_with("U123")
    user_profile_service.create_default_user_profile.assert_awaited_once_with(
        line_id="U123",
        display_name="Amy",
        picture_url=None,
    )


@pytest.mark.asyncio
async def test_login_with_id_token_skips_create_when_user_exists(monkeypatch):
    monkeypatch.setattr(
        "app.services.liff.auth_service.settings.LIFF_CHANNEL_ID",
        "liff-client-id",
    )
    monkeypatch.setattr(
        "app.services.liff.auth_service.settings.LINE_CHANNEL_ID",
        "line-client-id",
    )

    line_id_token_service = MagicMock()
    line_id_token_service.verify.return_value = {"sub": "U999", "name": "Bob"}

    jwt_service = MagicMock()
    jwt_service.issue_for_user.return_value = ("app-jwt", 1800)

    user_profile_service = MagicMock()
    user_profile_service.get_user_profile = AsyncMock(return_value={"line_id": "U999"})
    user_profile_service.create_default_user_profile = AsyncMock(return_value=True)

    service = LiffAuthApplicationService(
        line_id_token_service=line_id_token_service,
        jwt_service=jwt_service,
        user_profile_service=user_profile_service,
    )

    result = await service.login_with_id_token("id-token")

    assert result["line_user_id"] == "U999"
    user_profile_service.get_user_profile.assert_awaited_once_with("U999")
    user_profile_service.create_default_user_profile.assert_not_awaited()
