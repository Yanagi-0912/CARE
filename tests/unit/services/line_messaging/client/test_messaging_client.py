from unittest.mock import MagicMock, patch

import pytest

from app.services.line_messaging.client.messaging_client import LineMessagingClient


def test_reply_message_calls_line_sdk_with_access_token():
    client = LineMessagingClient()
    request = MagicMock()
    api_client_instance = MagicMock()
    api_client_cm = MagicMock()
    api_client_cm.__enter__.return_value = api_client_instance
    messaging_api = MagicMock()

    with patch(
        "app.services.line_messaging.client.messaging_client.Configuration"
    ) as mock_config, patch(
        "app.services.line_messaging.client.messaging_client.ApiClient",
        return_value=api_client_cm,
    ) as mock_api_client, patch(
        "app.services.line_messaging.client.messaging_client.MessagingApi",
        return_value=messaging_api,
    ) as mock_messaging_api:
        line_cfg = MagicMock()
        mock_config.return_value = line_cfg

        client.reply_message("token-123", request)

    mock_config.assert_called_once_with(access_token="token-123")
    mock_api_client.assert_called_once_with(line_cfg)
    mock_messaging_api.assert_called_once_with(api_client_instance)
    messaging_api.reply_message.assert_called_once_with(request)


def test_reply_message_propagates_sdk_error():
    client = LineMessagingClient()
    request = MagicMock()
    api_client_cm = MagicMock()
    api_client_cm.__enter__.return_value = MagicMock()
    messaging_api = MagicMock()
    messaging_api.reply_message.side_effect = RuntimeError("line sdk failed")

    with patch(
        "app.services.line_messaging.client.messaging_client.Configuration"
    ), patch(
        "app.services.line_messaging.client.messaging_client.ApiClient",
        return_value=api_client_cm,
    ), patch(
        "app.services.line_messaging.client.messaging_client.MessagingApi",
        return_value=messaging_api,
    ):
        with pytest.raises(RuntimeError, match="line sdk failed"):
            client.reply_message("token-123", request)
