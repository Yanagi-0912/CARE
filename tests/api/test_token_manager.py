import pytest
import requests
from app.services.line.token_manager import line_token_manager

@pytest.mark.integration
def test_get_token_returns_non_empty_string():
    token = line_token_manager.get_token() 
    assert isinstance(token, str)          #確認token是字串
    assert len(token) > 0                  #確認token不是空字串


@pytest.mark.integration
def test_get_token_valid_against_line_api():
    token = line_token_manager.get_token()
    response = requests.get(
        "https://api.line.me/v2/bot/info", # Line 官方 API 端點
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert response.status_code == 200
    data = response.json()
    assert "userId" in data and "displayName" in data
