"""LineLoadingAnimationService 單元測試。"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.line_messaging.loading_animation import (
    DEFAULT_LOADING_SECONDS,
    LineLoadingAnimationService,
)


@pytest.fixture
def token_manager():
    tm = MagicMock()
    tm.get_token.return_value = "test-token"
    return tm


@pytest.mark.asyncio
async def test_start_calls_show_loading_animation(token_manager):
    service = LineLoadingAnimationService(token_manager)

    with (
        patch(
            "app.services.line_messaging.loading_animation.Configuration"
        ) as mock_config,
        patch("app.services.line_messaging.loading_animation.ApiClient") as mock_api_client,
        patch(
            "app.services.line_messaging.loading_animation.MessagingApi"
        ) as mock_messaging_api,
    ):
        messaging_api = MagicMock()
        mock_messaging_api.return_value = messaging_api
        mock_api_client.return_value.__enter__.return_value = MagicMock()

        await service.start("U12345")

        mock_config.assert_called_once_with(access_token="test-token")
        messaging_api.show_loading_animation.assert_called_once()
        request = messaging_api.show_loading_animation.call_args[0][0]
        assert request.to_dict() == {
            "chatId": "U12345",
            "loadingSeconds": DEFAULT_LOADING_SECONDS,
        }


@pytest.mark.asyncio
async def test_start_skips_empty_chat_id(token_manager):
    service = LineLoadingAnimationService(token_manager)

    with patch(
        "app.services.line_messaging.loading_animation.MessagingApi"
    ) as mock_messaging_api:
        await service.start("  ")
        mock_messaging_api.assert_not_called()


@pytest.mark.asyncio
async def test_start_swallows_api_errors(token_manager):
    service = LineLoadingAnimationService(token_manager)
    token_manager.get_token.side_effect = RuntimeError("token failed")

    # 不應向外拋出
    await service.start("U12345")
