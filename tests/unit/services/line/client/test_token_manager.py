import pytest
from app.application.line.client import LineTokenManager
from app.infrastructure.line.shared.errors import LineTokenError


# 兩個測試邏輯完全一樣，只有 credential 值不同，用 parametrize 合併
@pytest.mark.parametrize(
    "channel_id, channel_secret",
    [
        (None, None),  # 未設定
        ("", ""),  # 空字串
    ],
)
def test_get_token_raises_when_credentials_invalid(channel_id, channel_secret):
    manager = LineTokenManager(
        channel_id=channel_id,
        channel_secret=channel_secret,
    )
    with pytest.raises(LineTokenError) as exc_info:
        manager.get_token()
    assert "LINE_CHANNEL_ID" in str(exc_info.value) or "LINE_CHANNEL_SECRET" in str(
        exc_info.value
    )
