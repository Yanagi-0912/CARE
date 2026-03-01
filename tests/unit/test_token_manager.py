"""LINE Token Manager 單元測試：mock 外部依賴，不打真實 LINE API."""
import pytest
from unittest.mock import patch

from app.services.line.token_manager import LineTokenManager


def test_get_token_raises_when_credentials_missing():
    """未設定 CHANNEL_ID / CHANNEL_SECRET 時，get_token 應拋出 ValueError."""
    with patch("app.services.line.token_manager.settings") as mock_settings:
        mock_settings.LINE_CHANNEL_ID = None
        mock_settings.LINE_CHANNEL_SECRET = None
        manager = LineTokenManager()

    with pytest.raises(ValueError) as exc_info:
        manager.get_token()

    assert "LINE_CHANNEL_ID" in str(exc_info.value) or "LINE_CHANNEL_SECRET" in str(exc_info.value)


def test_get_token_raises_when_empty_string_credentials():
    """CHANNEL_ID / CHANNEL_SECRET 為空字串時，get_token 應拋出 ValueError."""
    with patch("app.services.line.token_manager.settings") as mock_settings:
        mock_settings.LINE_CHANNEL_ID = ""
        mock_settings.LINE_CHANNEL_SECRET = ""
        manager = LineTokenManager()

    with pytest.raises(ValueError):
        manager.get_token()
