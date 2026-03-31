from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.application.rag.client.embedding_client import embed_document, embed_query


@pytest.mark.asyncio
async def test_embed_query_success():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"embedding": {"values": [1, 2.5, 3]}}

    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=resp)
    http_client_cm = MagicMock()
    http_client_cm.__aenter__ = AsyncMock(return_value=http_client)
    http_client_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.application.rag.client.embedding_client.httpx.AsyncClient",
        return_value=http_client_cm,
    ), patch("app.application.rag.client.embedding_client.settings") as mock_settings:
        mock_settings.GEMINI_API_KEY = "k"
        mock_settings.EMBEDDING_MODEL = "gemini-embedding-001"
        mock_settings.MONGODB_VECTOR_DIM = 3

        out = await embed_query("高血壓")

    assert out == [1.0, 2.5, 3.0]
    http_client.post.assert_awaited_once()
    called = http_client.post.await_args
    assert "embedContent" in called.args[0]
    assert called.kwargs["params"] == {"key": "k"}
    assert called.kwargs["json"]["taskType"] == "RETRIEVAL_QUERY"
    assert called.kwargs["json"]["outputDimensionality"] == 3


@pytest.mark.asyncio
async def test_embed_document_rejects_empty_text():
    with pytest.raises(ValueError, match="cannot be empty"):
        await embed_document("   ")


@pytest.mark.asyncio
async def test_embed_query_maps_request_error():
    http_client = MagicMock()
    http_client.post = AsyncMock(
        side_effect=httpx.RequestError("network down", request=MagicMock())
    )
    http_client_cm = MagicMock()
    http_client_cm.__aenter__ = AsyncMock(return_value=http_client)
    http_client_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.application.rag.client.embedding_client.httpx.AsyncClient",
        return_value=http_client_cm,
    ), patch("app.application.rag.client.embedding_client.settings") as mock_settings:
        mock_settings.GEMINI_API_KEY = "k"
        mock_settings.EMBEDDING_MODEL = "gemini-embedding-001"
        mock_settings.MONGODB_VECTOR_DIM = 0

        with pytest.raises(ValueError, match="請求失敗"):
            await embed_query("正常文字")


@pytest.mark.asyncio
async def test_embed_query_raises_when_response_dim_mismatch():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"embedding": {"values": [0.1, 0.2]}}

    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=resp)
    http_client_cm = MagicMock()
    http_client_cm.__aenter__ = AsyncMock(return_value=http_client)
    http_client_cm.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.application.rag.client.embedding_client.httpx.AsyncClient",
        return_value=http_client_cm,
    ), patch("app.application.rag.client.embedding_client.settings") as mock_settings:
        mock_settings.GEMINI_API_KEY = "k"
        mock_settings.EMBEDDING_MODEL = "gemini-embedding-001"
        mock_settings.MONGODB_VECTOR_DIM = 3

        with pytest.raises(ValueError, match="維度不一致"):
            await embed_query("正常文字")
