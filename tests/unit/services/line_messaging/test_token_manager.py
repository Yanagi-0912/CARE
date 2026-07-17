from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.line_messaging.reply.reply import LineTokenManager


@pytest.mark.parametrize(
    "channel_id, channel_secret",
    [
        (None, None),
        ("", ""),
    ],
)
def test_get_token_raises_when_credentials_invalid(channel_id, channel_secret):
    token_manager = LineTokenManager(
        channel_id=channel_id,
        channel_secret=channel_secret,
    )
    with pytest.raises(ValueError) as exc_info:
        token_manager.get_token()
    assert "LINE_CHANNEL_ID" in str(exc_info.value) or "LINE_CHANNEL_SECRET" in str(
        exc_info.value
    )


def test_get_token_fetches_and_caches_token():
    token_manager = LineTokenManager(channel_id="cid", channel_secret="secret")

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "access_token": "new-token",
        "expires_in": 3600,
    }

    with patch(
        "app.services.line_messaging.reply.reply.requests.post",
        return_value=mock_response,
    ) as mock_post:
        first = token_manager.get_token()
        second = token_manager.get_token()

    assert first == "new-token"
    assert second == "new-token"
    mock_post.assert_called_once()


def test_get_token_refreshes_when_cache_expired():
    token_manager = LineTokenManager(channel_id="cid", channel_secret="secret")
    token_manager._access_token = "old-token"
    token_manager._token_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "access_token": "refreshed-token",
        "expires_in": 3600,
    }

    with patch(
        "app.services.line_messaging.reply.reply.requests.post",
        return_value=mock_response,
    ) as mock_post:
        token = token_manager.get_token()

    assert token == "refreshed-token"
    mock_post.assert_called_once()
