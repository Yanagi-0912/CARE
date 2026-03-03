"""LINE Token Manager API/整合測試：打真實 LINE API，需 .env 設定 CHANNEL_ID / CHANNEL_SECRET."""
import os
import pytest
import requests

from app.services.line.token_manager import line_token_manager


def _has_line_credentials():#檢查我在env 裡面有沒有設token
    return bool(
        os.getenv("LINE_CHANNEL_ID") and os.getenv("LINE_CHANNEL_SECRET")
    )


@pytest.mark.skipif(
    not _has_line_credentials(),
    reason="Need LINE_CHANNEL_ID and LINE_CHANNEL_SECRET in .env",
)
def test_get_token_returns_non_empty_string():
    token = line_token_manager.get_token()#跟真正的lineOAuth伺服器要access token
    assert isinstance(token, str)#確認回傳 token是字串
    assert len(token) > 0#確認回傳token不是空字串


@pytest.mark.skipif(
    not _has_line_credentials(),
    reason="Need LINE_CHANNEL_ID and LINE_CHANNEL_SECRET in .env",
)
def test_get_token_valid_against_line_api():
    token = line_token_manager.get_token()
    resp = requests.get(
        "https://api.line.me/v2/bot/info",#這個你在router 裡面找不到，不是router定義的endpoint，這是line 的API
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert resp.status_code == 200#確定你的token 真的可以拿到資訊，token有效被line 接受
    data = resp.json()
    assert "userId" in data or "displayName" in data#確認有拿到 bot的資訊
