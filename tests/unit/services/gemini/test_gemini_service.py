import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.gemini import GeminiService, GeminiHttpError


@pytest.fixture
def mock_settings():
    with patch("app.services.gemini.client.service.settings") as m:
        m.GEMINI_API_KEY = "test_key"
        m.MODEL_NAME = "gemini-2.0-flash"
        yield m


@pytest.fixture
def mock_http_client():
    with patch("app.services.gemini.client.service.httpx.AsyncClient") as mock_ac:
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

    assert result.text == "AI 回覆內容"
    assert post.called  # 確定 generate_response 有去呼叫 post


@pytest.mark.asyncio
async def test_generate_response_returns_validation_error_without_api_call(
    mock_settings, mock_http_client
):
    response = MagicMock()
    response.status_code = 200
    post = mock_http_client(response)

    result = await GeminiService().generate_response("   ")

    assert result.text == "請輸入訊息內容，不能為空白。"
    assert result.is_function_call is False
    assert not post.called


@pytest.mark.asyncio
async def test_generate_response_returns_function_call_from_model(
    mock_settings, mock_http_client
):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "functionCall": {
                                "name": "request_location",
                                "args": {},
                            }
                        }
                    ]
                }
            }
        ]
    }
    mock_http_client(response)

    result = await GeminiService().generate_response("附近哪裡有醫院")

    assert result.is_function_call is True
    assert result.function_name == "request_location"
    assert result.function_args == {}


@pytest.mark.asyncio
async def test_generate_response_raises_value_error_on_4xx(
    mock_settings, mock_http_client
):
    # 準備假 response：429 配額超限
    response = MagicMock()
    response.status_code = 429
    response.text = "quota exceeded"
    mock_http_client(response)

    with pytest.raises(GeminiHttpError) as exc_info:
        await GeminiService().generate_response("hi")

    assert "配額" in str(exc_info.value) or "429" in str(exc_info.value)
