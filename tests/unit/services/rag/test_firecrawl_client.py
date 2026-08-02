from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.rag.firecrawl_client import FirecrawlClient


def _mock_response(payload: dict, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json = MagicMock(return_value=payload)
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "err",
                request=MagicMock(),
                response=MagicMock(status_code=status_code),
            )
        )
    return response


@pytest.mark.asyncio
async def test_search_parses_hits_from_firecrawl_payload():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        return_value=_mock_response(
            {
                "success": True,
                "data": [
                    {
                        "title": "高血壓",
                        "description": "說明",
                        "url": "https://www.hpa.gov.tw/a",
                    }
                ],
            }
        )
    )
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    hits = await client.search("高血壓", limit=3)
    assert len(hits) == 1
    assert hits[0].title == "高血壓"
    assert hits[0].url == "https://www.hpa.gov.tw/a"
    http_client.post.assert_awaited_once()
    args, kwargs = http_client.post.await_args
    assert args[0].endswith("/search")
    assert kwargs["headers"]["Authorization"] == "Bearer fc-test"
    assert kwargs["json"] == {"query": "高血壓", "limit": 3}


@pytest.mark.asyncio
async def test_scrape_returns_markdown_text():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        return_value=_mock_response(
            {"success": True, "data": {"markdown": "# 標題\n內容"}}
        )
    )
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    text = await client.scrape("https://www.hpa.gov.tw/a")
    assert "內容" in text
    args, kwargs = http_client.post.await_args
    assert args[0].endswith("/scrape")
    assert kwargs["json"]["url"] == "https://www.hpa.gov.tw/a"
    assert "markdown" in kwargs["json"]["formats"]


@pytest.mark.asyncio
async def test_search_returns_empty_when_api_key_missing():
    http_client = AsyncMock()
    client = FirecrawlClient(api_key="", http_client=http_client)
    assert await client.search("q") == []
    http_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_returns_empty_on_http_error():
    http_client = AsyncMock()
    http_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    assert await client.search("q") == []


@pytest.mark.asyncio
async def test_scrape_returns_empty_on_http_error():
    http_client = AsyncMock()
    http_client.post = AsyncMock(side_effect=httpx.ConnectError("down"))
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    assert await client.scrape("https://www.hpa.gov.tw/a") == ""


@pytest.mark.asyncio
async def test_scrape_uses_longer_timeout_than_search():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        return_value=_mock_response({"success": True, "data": {"markdown": "ok"}})
    )
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    await client.scrape("https://www.hpa.gov.tw/a")
    _args, kwargs = http_client.post.await_args
    assert kwargs["timeout"] == 45.0


@pytest.mark.asyncio
async def test_scrape_timeout_returns_empty_without_raising():
    http_client = AsyncMock()
    http_client.post = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    assert await client.scrape("https://www.hpa.gov.tw/a") == ""
