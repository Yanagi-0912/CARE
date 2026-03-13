import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.gemini_service import GeminiService


@pytest.fixture
def mock_settings():
    with patch("app.services.gemini_service.settings") as m:
        m.GEMINI_API_KEY = "test_key"
        m.MODEL_NAME = "gemini-2.0-flash"
        yield m


@pytest.fixture
def mock_http_client():
    with patch("app.services.gemini_service.httpx.AsyncClient") as mock_ac:
        mock_ac.return_value.__aexit__ = AsyncMock(return_value=None)

        def configure(response):
            post = AsyncMock(return_value=response)
            client_instance = MagicMock()
            client_instance.post = post
            mock_ac.return_value.__aenter__ = AsyncMock(return_value=client_instance)
            return post

        yield configure


@pytest.mark.asyncio
async def test_generate_response_returns_text_on_success(
    mock_settings, mock_http_client
):
    # 準備假 response：200 成功
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "AI 回覆內容"}]}}]
    }
    post = mock_http_client(response)

    result = await GeminiService().generate_response("你好")

    assert result == "AI 回覆內容"
    assert post.called  # 確定 generate_response 有去呼叫 post


@pytest.mark.asyncio
async def test_generate_response_raises_value_error_on_4xx(
    mock_settings, mock_http_client
):
    # 準備假 response：429 配額超限
    response = MagicMock()
    response.status_code = 429
    response.text = "quota exceeded"
    mock_http_client(response)

    with pytest.raises(ValueError) as exc_info:
        await GeminiService().generate_response("hi")

    assert "配額" in str(exc_info.value) or "429" in str(exc_info.value)
