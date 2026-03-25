import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.gemini import GeminiHttpError, GeminiService


class _FakeClientContext:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def service_factory():
    def _build(response):
        post = AsyncMock(return_value=response)
        client = MagicMock()
        client.post = post
        svc = GeminiService(
            api_key="test_key",
            model_name="gemini-2.0-flash",
            http_client_factory=lambda timeout: _FakeClientContext(client),
        )
        return svc, post

    return _build


@pytest.mark.asyncio
async def test_generate_response_returns_text_on_success(service_factory):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "AI 回覆內容"}]}}]
    }
    svc, post = service_factory(response)

    result = await svc.generate_response("你好")

    assert result.text == "AI 回覆內容"
    assert post.called


@pytest.mark.asyncio
async def test_generate_response_returns_validation_error_without_api_call(
    service_factory,
):
    response = MagicMock()
    response.status_code = 200
    svc, post = service_factory(response)

    result = await svc.generate_response("   ")

    assert result.text == "請輸入訊息內容，不能為空白。"
    assert result.is_function_call is False
    assert not post.called


@pytest.mark.asyncio
async def test_generate_response_returns_function_call_from_model(service_factory):
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
    svc, _ = service_factory(response)

    result = await svc.generate_response("附近哪裡有醫院")

    assert result.is_function_call is True
    assert result.function_name == "request_location"
    assert result.function_args == {}


@pytest.mark.asyncio
async def test_generate_response_raises_http_error_on_4xx(service_factory):
    response = MagicMock()
    response.status_code = 429
    response.text = "quota exceeded"
    svc, _ = service_factory(response)

    with pytest.raises(GeminiHttpError) as exc_info:
        await svc.generate_response("hi")

    assert "配額" in str(exc_info.value) or "429" in str(exc_info.value)
