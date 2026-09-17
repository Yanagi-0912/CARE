import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.rag.firecrawl_client import FirecrawlClient
from app.services.rag.web_client import WebSearchUnavailable


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
                "data": {
                    "web": [
                        {
                            "title": "高血壓",
                            "description": "說明",
                            "url": "https://www.hpa.gov.tw/a",
                        }
                    ]
                },
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
    assert args[0] == "https://api.firecrawl.dev/v2/search"
    assert kwargs["headers"]["Authorization"] == "Bearer fc-test"
    assert kwargs["json"] == {"query": "高血壓", "limit": 3}


@pytest.mark.asyncio
async def test_search_sends_include_domains_when_given():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        return_value=_mock_response({"success": True, "data": {"web": []}})
    )
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    await client.search(
        "persistent genital arousal disorder",
        limit=8,
        include_domains=["nih.gov", "medlineplus.gov"],
    )
    _args, kwargs = http_client.post.await_args
    assert kwargs["json"] == {
        "query": "persistent genital arousal disorder",
        "limit": 8,
        "includeDomains": ["nih.gov", "medlineplus.gov"],
    }


@pytest.mark.asyncio
async def test_search_still_parses_v1_list_payload():
    """base_url 被指回 v1 時 data 是 list；只認 v2 的形狀會靜靜回 0 筆。"""
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        return_value=_mock_response(
            {
                "success": True,
                "data": [{"title": "高血壓", "url": "https://www.hpa.gov.tw/a"}],
            }
        )
    )
    client = FirecrawlClient(
        api_key="fc-test",
        base_url="https://api.firecrawl.dev/v1",
        http_client=http_client,
    )
    hits = await client.search("高血壓")
    assert [hit.url for hit in hits] == ["https://www.hpa.gov.tw/a"]


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
async def test_search_raises_unavailable_on_timeout():
    """逾時是「沒搜成」，不能回空 list 讓呼叫端當成「搜了沒有」。"""
    http_client = AsyncMock()
    http_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    with pytest.raises(WebSearchUnavailable) as exc_info:
        await client.search("q")
    assert exc_info.value.status is None
    assert exc_info.value.rate_limited is False


@pytest.mark.asyncio
async def test_search_raises_unavailable_with_status_on_429():
    """限流要帶著狀態碼往上：呼叫端靠它決定不重搜、對使用者說「太頻繁」。"""
    http_client = AsyncMock()
    http_client.post = AsyncMock(return_value=_mock_response({}, status_code=429))
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    with pytest.raises(WebSearchUnavailable) as exc_info:
        await client.search("q")
    assert exc_info.value.status == 429
    assert exc_info.value.rate_limited is True


@pytest.mark.asyncio
async def test_search_raises_unavailable_on_5xx():
    http_client = AsyncMock()
    http_client.post = AsyncMock(return_value=_mock_response({}, status_code=503))
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    with pytest.raises(WebSearchUnavailable) as exc_info:
        await client.search("q")
    assert exc_info.value.status == 503


@pytest.mark.asyncio
async def test_search_empty_payload_is_empty_not_unavailable():
    """真的搜到 0 筆仍回空 list，呼叫端才會走「找不到」而不是「服務失敗」。"""
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        return_value=_mock_response({"success": True, "data": {"web": []}})
    )
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


@pytest.mark.asyncio
async def test_scrape_page_returns_final_url_from_metadata():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        return_value=_mock_response(
            {
                "success": True,
                "data": {
                    "markdown": "# 標題\n內容",
                    "metadata": {"url": "https://www.hpa.gov.tw/final"},
                },
            }
        )
    )
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    page = await client.scrape_page("https://www.hpa.gov.tw/a")
    assert page.final_url == "https://www.hpa.gov.tw/final"
    assert "內容" in page.text


@pytest.mark.asyncio
async def test_scrape_page_returns_none_final_url_when_metadata_missing():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        return_value=_mock_response(
            {"success": True, "data": {"markdown": "# 標題\n內容"}}
        )
    )
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    page = await client.scrape_page("https://www.hpa.gov.tw/a")
    assert page.final_url is None
    assert "內容" in page.text


@pytest.mark.asyncio
async def test_gate_caps_requests_in_flight_at_max_concurrency():
    """併發閘要真的擋住第 N+1 個請求，而不是只是宣稱有上限。

    Hobby 的併發上限是 5，超過拿到的是 429——search 會變成「搜尋服務暫時
    無法使用」、scrape 則靜靜回空字串。這裡直接量在途數的峰值。
    """
    in_flight = 0
    peak = 0

    async def slow_post(*args, **kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.01)
        finally:
            in_flight -= 1
        return _mock_response({"success": True, "data": {"markdown": "x"}})

    http_client = AsyncMock()
    http_client.post = slow_post
    client = FirecrawlClient(
        api_key="fc-test", http_client=http_client, max_concurrency=2
    )

    await asyncio.gather(*(client.scrape(f"https://x.gov.tw/{i}") for i in range(6)))

    assert peak == 2


def test_gate_is_rebuilt_for_a_new_event_loop():
    """同一個客戶端實例跨 event loop 重用不能爆掉。

    dependencies.py 在模組層建立這個客戶端，而測試裡每個 asyncio.run() 都是
    新 loop；Semaphore 綁死在第一個 loop 上會拋「bound to a different event
    loop」，正式環境看不到、測試一跑就炸。
    """
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        return_value=_mock_response({"success": True, "data": {"markdown": "x"}})
    )
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)

    assert asyncio.run(client.scrape("https://x.gov.tw/a")) == "x"
    assert asyncio.run(client.scrape("https://x.gov.tw/b")) == "x"


@pytest.mark.asyncio
async def test_connection_pool_is_reused_across_calls():
    """同一個客戶端的多次呼叫共用一條連線池。

    以前每次呼叫都新建 AsyncClient、用完就關，等於每次 search／scrape 都重做
    DNS 解析與 TLS 握手。實測第一次 search 9.4 秒、之後四次 1.4–2.5 秒。
    """
    client = FirecrawlClient(api_key="fc-test")
    first = client._client()
    second = client._client()
    assert first is second
    await first.aclose()


@pytest.mark.asyncio
async def test_injected_http_client_still_wins():
    """注入的 client 優先，不會被共用連線池取代（測試靠這個攔請求）。"""
    injected = AsyncMock()
    client = FirecrawlClient(api_key="fc-test", http_client=injected)
    assert client._client() is injected
