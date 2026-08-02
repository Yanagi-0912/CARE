from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.dependencies import CurrentUser, require_admin_user


@pytest.mark.asyncio
async def test_require_admin_user_allows_admin():
    profile_service = MagicMock()
    profile_service.get_user_profile = AsyncMock(return_value={"role": "admin"})
    current_user = CurrentUser(line_user_id="U_ADMIN")

    result = await require_admin_user(
        current_user=current_user,
        user_profile_service=profile_service,
    )

    assert result == current_user
    profile_service.get_user_profile.assert_awaited_once_with("U_ADMIN")


@pytest.mark.asyncio
async def test_require_admin_user_rejects_user_role():
    profile_service = MagicMock()
    profile_service.get_user_profile = AsyncMock(return_value={"role": "user"})
    current_user = CurrentUser(line_user_id="U_USER")

    with pytest.raises(HTTPException) as exc_info:
        await require_admin_user(
            current_user=current_user,
            user_profile_service=profile_service,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_admin_user_treats_missing_role_as_user():
    profile_service = MagicMock()
    profile_service.get_user_profile = AsyncMock(return_value={"name": "Amy"})
    current_user = CurrentUser(line_user_id="U_NO_ROLE")

    with pytest.raises(HTTPException) as exc_info:
        await require_admin_user(
            current_user=current_user,
            user_profile_service=profile_service,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_require_admin_user_rejects_missing_profile():
    profile_service = MagicMock()
    profile_service.get_user_profile = AsyncMock(return_value=None)
    current_user = CurrentUser(line_user_id="U_MISSING")

    with pytest.raises(HTTPException) as exc_info:
        await require_admin_user(
            current_user=current_user,
            user_profile_service=profile_service,
        )

    assert exc_info.value.status_code == 403
