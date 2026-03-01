"""LINE Token Manager API/整合測試：打真實 LINE API，需 .env 設定 CHANNEL_ID / CHANNEL_SECRET."""
import os
import pytest
import requests

from app.services.line.token_manager import line_token_manager


def _has_line_credentials():
    return bool(
        os.getenv("LINE_CHANNEL_ID") and os.getenv("LINE_CHANNEL_SECRET")
    )


@pytest.mark.skipif(
    not _has_line_credentials(),
    reason="Need LINE_CHANNEL_ID and LINE_CHANNEL_SECRET in .env",
)
def test_get_token_returns_non_empty_string():
    """有設定時，get_token 應回傳非空字串."""
    token = line_token_manager.get_token()
    assert isinstance(token, str)
    assert len(token) > 0


@pytest.mark.skipif(
    not _has_line_credentials(),
    reason="Need LINE_CHANNEL_ID and LINE_CHANNEL_SECRET in .env",
)
def test_get_token_valid_against_line_api():
    """取得的 token 用 LINE /v2/bot/info 驗證應為有效."""
    token = line_token_manager.get_token()
    resp = requests.get(
        "https://api.line.me/v2/bot/info",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "userId" in data or "displayName" in data
