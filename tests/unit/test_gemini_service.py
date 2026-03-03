from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from app.services.gemini_service import GeminiService
#單元測試：mock httpx，不打真實 Gemini API
@patch("app.services.gemini_service.settings")
@pytest.mark.asyncio
async def test_generate_response_returns_text_on_success(mock_settings):#確定service有沒有去呼叫post
    mock_settings.GEMINI_API_KEY = "test_key"
    mock_settings.MODEL_NAME = "gemini-2.0-flash"
    response = MagicMock()#這叫做萬用假物件，可以假裝成任何物件
    response.status_code = 200#假裝200 成功
    response.json.return_value = {
        "candidates": [
            {"content": {"parts": [{"text": "AI 回覆內容"}]}}
        ]
    }
    post = AsyncMock(return_value=response)#把真正的 httpx.AsyncClient 替換成假method
    client_instance = MagicMock()
    client_instance.post = post

    with patch("app.services.gemini_service.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value.__aenter__ = AsyncMock(return_value=client_instance)
        mock_ac.return_value.__aexit__ = AsyncMock(return_value=None)
        service = GeminiService()
        result = await service.generate_response("你好")#這會去呼叫client.post

    assert result == "AI 回覆內容"
    assert post.called#如果generate_response沒有去呼叫post，就會是false


@patch("app.services.gemini_service.settings")
@pytest.mark.asyncio
async def test_generate_response_raises_value_error_on_4xx(mock_settings):
    mock_settings.GEMINI_API_KEY = "test_key"
    mock_settings.MODEL_NAME = "gemini-2.0-flash"
    response = MagicMock()
    response.status_code = 429#當gemini回傳429 配額超限
    response.text = "quota exceeded"
    post = AsyncMock(return_value=response)
    client_instance = MagicMock()
    client_instance.post = post

    with patch("app.services.gemini_service.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value.__aenter__ = AsyncMock(return_value=client_instance)
        mock_ac.return_value.__aexit__ = AsyncMock(return_value=None)
        service = GeminiService()
        with pytest.raises(ValueError) as exc_info:
            await service.generate_response("hi")
        assert "配額" in str(exc_info.value) or "429" in str(exc_info.value)#確定在配額不足時候 有正常拋出錯誤訊息
