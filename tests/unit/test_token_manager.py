import pytest
from unittest.mock import patch
from app.services.line.token_manager import LineTokenManager


# 兩個測試邏輯完全一樣，只有 credential 值不同，用 parametrize 合併
@pytest.mark.parametrize("channel_id, channel_secret", [
    (None, None),  # 未設定
    ("",   ""),    # 空字串
])
def test_get_token_raises_when_credentials_invalid(channel_id, channel_secret):
    with patch("app.services.line.token_manager.settings") as mock_settings:
        mock_settings.LINE_CHANNEL_ID = channel_id
        mock_settings.LINE_CHANNEL_SECRET = channel_secret
        manager = LineTokenManager()  # __init__ 在此讀取 settings，需在 patch 內建立
    with pytest.raises(ValueError) as exc_info:
        manager.get_token()
    assert "LINE_CHANNEL_ID" in str(exc_info.value) or "LINE_CHANNEL_SECRET" in str(exc_info.value)