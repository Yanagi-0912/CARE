import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.infrastructure.gemini.client.gemini_client import GeminiClient
from app.infrastructure.gemini.shared.errors import (
    GeminiHttpError,
    GeminiNetworkError,
    GeminiSchemaError,
    GeminiUnknownError,
)


class _FakeClientContext:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.fixture
def http_factory():
    def _make(mock_http_client):
        return lambda timeout: _FakeClientContext(mock_http_client)

    return _make


@pytest.mark.asyncio
async def test_generate_content_returns_json_on_200(http_factory):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"candidates": []}

    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=response)

    client = GeminiClient(
        api_key="test_key",
        model_name="gemini-2.0-flash",
        http_client_factory=http_factory(http_client),
    )

    out = await client.generate_content({"contents": []}, timeout=30.0)

    assert out == {"candidates": []}
    http_client.post.assert_awaited_once()
    call_kw = http_client.post.await_args
    assert call_kw.kwargs["params"] == {"key": "test_key"}
    assert "generativelanguage.googleapis.com" in call_kw.args[0]


@pytest.mark.asyncio
async def test_generate_content_raises_http_error_on_non_200(http_factory):
    response = MagicMock()
    response.status_code = 429
    response.text = "quota"

    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=response)

    client = GeminiClient(
        api_key="k",
        model_name="m",
        http_client_factory=http_factory(http_client),
    )

    with pytest.raises(GeminiHttpError) as exc_info:
        await client.generate_content({})

    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_generate_content_raises_network_error_on_timeout(http_factory):
    http_client = MagicMock()
    http_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

    client = GeminiClient(
        api_key="k",
        model_name="m",
        http_client_factory=http_factory(http_client),
    )

    with pytest.raises(GeminiNetworkError, match="超時"):
        await client.generate_content({})


@pytest.mark.asyncio
async def test_generate_content_raises_network_error_on_connect_error(http_factory):
    http_client = MagicMock()
    http_client.post = AsyncMock(
        side_effect=httpx.ConnectError("connection refused")
    )

    client = GeminiClient(
        api_key="k",
        model_name="m",
        http_client_factory=http_factory(http_client),
    )

    with pytest.raises(GeminiNetworkError, match="無法連線"):
        await client.generate_content({})


@pytest.mark.asyncio
async def test_generate_content_raises_schema_error_when_json_raises_keyerror(
    http_factory,
):
    response = MagicMock()
    response.status_code = 200
    response.json.side_effect = KeyError("missing")

    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=response)

    client = GeminiClient(
        api_key="k",
        model_name="m",
        http_client_factory=http_factory(http_client),
    )

    with pytest.raises(GeminiSchemaError, match="缺少欄位"):
        await client.generate_content({})


@pytest.mark.asyncio
async def test_generate_content_raises_unknown_on_unexpected_exception(http_factory):
    http_client = MagicMock()
    http_client.post = AsyncMock(side_effect=RuntimeError("boom"))

    client = GeminiClient(
        api_key="k",
        model_name="m",
        http_client_factory=http_factory(http_client),
    )

    with pytest.raises(GeminiUnknownError, match="boom"):
        await client.generate_content({})
