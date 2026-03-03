"""LINE Token Manager 單元測試：mock 外部依賴，不打真實 LINE API."""
import pytest
from unittest.mock import patch

from app.services.line.token_manager import LineTokenManager

#如果line憑證沒設定，get_token 應拋出 ValueError
def test_get_token_raises_when_credentials_missing():
    with patch("app.services.line.token_manager.settings") as mock_settings:
        mock_settings.LINE_CHANNEL_ID = None
        mock_settings.LINE_CHANNEL_SECRET = None#沒設定憑證
        manager = LineTokenManager()
    with pytest.raises(ValueError) as exc_info:
        manager.get_token()#建立完line token manager 物件後，get_token 會去呼叫_fetch_new_token
    assert "LINE_CHANNEL_ID" in str(exc_info.value) or "LINE_CHANNEL_SECRET" in str(exc_info.value)#確定有拋出錯誤訊息
#錯誤訊息要提到line_channel_id 和 line_channel_secret 就是fetch_new_token 的 value error

def test_get_token_raises_when_empty_string_credentials():
    #CHANNEL_ID / CHANNEL_SECRET 為空字串時，get_token 應拋出 ValueError
    with patch("app.services.line.token_manager.settings") as mock_settings:
        mock_settings.LINE_CHANNEL_ID = ""
        mock_settings.LINE_CHANNEL_SECRET = ""
        manager = LineTokenManager()

    with pytest.raises(ValueError):#確定有拋出錯誤訊息
        manager.get_token()
