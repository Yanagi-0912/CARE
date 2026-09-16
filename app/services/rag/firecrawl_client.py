from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any

import httpx

from app.services.rag.web_client import (
    ScrapedPage,
    WebSearchHit,
    WebSearchUnavailable,
)

logger = logging.getLogger(__name__)

# 同時在途的 Firecrawl 請求上限，search 與 scrape 共用一個數。
#
# 5 ＝ 目前方案（Hobby）的併發上限。超過的請求拿到的是 429，而 429 在這個
# 客戶端的兩條路都不好收拾：search 會變成 WebSearchUnavailable（使用者看到
# 「搜尋服務暫時無法使用」），scrape 則是靜靜回空字串、從外面看不出與
# 「這頁沒內文」的差別。排隊多等幾百毫秒比這兩種都好，所以寧可在客戶端先擋。
#
# **這個閘是 per-process 的**：backend 與 scheduler 是兩個 pod，各自有一份，
# 兩邊同時爆發時帳號層級仍可能超過 5。scheduler 這邊只有每日醫療消息在用
# （一天一次），重疊機率低；真撞上了還是由既有的 429 處置接手，不會更糟。
DEFAULT_MAX_CONCURRENCY = 5


class FirecrawlClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.firecrawl.dev/v2",
        timeout_seconds: float = 15.0,
        scrape_timeout_seconds: float | None = None,
        http_client: httpx.AsyncClient | None = None,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        # scrape 常比 search 慢；預設加長，避免 15s ReadTimeout 連續失敗
        self._scrape_timeout_seconds = (
            scrape_timeout_seconds
            if scrape_timeout_seconds is not None
            else max(timeout_seconds, 45.0)
        )
        self._http_client = http_client
        self._max_concurrency = max(1, max_concurrency)
        self._gate_loop: asyncio.AbstractEventLoop | None = None
        self._gate_semaphore: asyncio.Semaphore | None = None

    def _gate(self) -> asyncio.Semaphore:
        """併發閘，綁在目前的 event loop 上。

        不在 `__init__` 就建好：這個客戶端是在 dependencies.py 的模組層建立的
        （當下沒有 running loop），而測試裡每個 `asyncio.run()` 都是新 loop，
        跨 loop 重用同一個 Semaphore 會拋「bound to a different event loop」。
        只留一格快取而不是用字典存所有 loop——正式環境只有一個 loop，測試的
        loop 用完即棄，留著只會把它們釘在記憶體裡。
        """
        loop = asyncio.get_running_loop()
        if self._gate_semaphore is None or self._gate_loop is not loop:
            self._gate_semaphore = asyncio.Semaphore(self._max_concurrency)
            self._gate_loop = loop
        return self._gate_semaphore

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        include_domains: Sequence[str] | None = None,
    ) -> list[WebSearchHit]:
        if not self._api_key:
            return []
        body: dict[str, Any] = {"query": query, "limit": limit}
        if include_domains:
            # v2 原生的網域限制，官方文件說它是在 query 內部加上 site: 運算子。
            # 用它而不自己拼 `site:A OR site:B`：OR 不在官方文件的運算子清單上，
            # 能用只是實測剛好可以。
            body["includeDomains"] = list(include_domains)
        client = self._http_client or httpx.AsyncClient(timeout=self._timeout_seconds)
        owns_client = self._http_client is None
        # 失敗一律拋 WebSearchUnavailable，不回空 list：空 list 在呼叫端的意思是
        # 「搜了、沒有」，會走「找不到」的文案；429／逾時／5xx 是「沒搜成」，
        # 兩者的處置不同（理由見 web_client.WebSearchUnavailable）。
        try:
            # 閘只圈住實際的網路往返：raise_for_status／json() 都是在本地對
            # 已讀完的回應做事，圈進來只會白白多佔一個併發名額。
            async with self._gate():
                response = await client.post(
                    f"{self._base_url}/search",
                    headers=self._headers(),
                    json=body,
                    timeout=self._timeout_seconds,
                )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            # 429 只記 warning：免費方案的額度是已知的營運限制，不是程式錯誤，
            # 每次都印 traceback 只會淹掉真正的例外。
            logger.warning("Firecrawl search failed status=%s", status)
            raise WebSearchUnavailable(f"http_{status}", status=status) from exc
        except httpx.TimeoutException as exc:
            logger.warning(
                "Firecrawl search timeout timeout_s=%s", self._timeout_seconds
            )
            raise WebSearchUnavailable("timeout") from exc
        except Exception as exc:
            logger.exception("Firecrawl search failed")
            raise WebSearchUnavailable(type(exc).__name__) from exc
        finally:
            if owns_client:
                await client.aclose()

        data = payload.get("data") if isinstance(payload, dict) else None
        # v2 的 data 是 {"web": [...], "news": ..., "images": ...}，v1 是 list。
        # 兩種都認：只認一種的話，base_url 與格式對不上時會「成功回應、0 筆
        # 結果」，不報錯也不留 log，網搜就這樣靜靜失效。
        items = data.get("web") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        hits: list[WebSearchHit] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            hits.append(
                WebSearchHit(
                    title=str(item.get("title") or "").strip(),
                    url=url,
                    description=str(item.get("description") or "").strip(),
                )
            )
        return hits

    async def scrape_page(self, url: str) -> ScrapedPage:
        if not self._api_key:
            return ScrapedPage(text="", final_url=None)
        timeout = self._scrape_timeout_seconds
        client = self._http_client or httpx.AsyncClient(timeout=timeout)
        owns_client = self._http_client is None
        try:
            async with self._gate():
                response = await client.post(
                    f"{self._base_url}/scrape",
                    headers=self._headers(),
                    json={"url": url, "formats": ["markdown"]},
                    timeout=timeout,
                )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException:
            logger.warning(
                "Firecrawl scrape timeout url=%s timeout_s=%s",
                url,
                timeout,
            )
            return ScrapedPage(text="", final_url=None)
        except Exception:
            logger.exception("Firecrawl scrape failed url=%s", url)
            return ScrapedPage(text="", final_url=None)
        finally:
            if owns_client:
                await client.aclose()

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return ScrapedPage(text="", final_url=None)

        text = str(data.get("markdown") or "").strip()

        # metadata 可能不是 dict（甚至不存在），取值前要防禦；依序試
        # metadata.url、metadata.sourceURL，皆無則 final_url 為 None
        # （design.md Decision 8 的 fail-open，由呼叫端 log 並決定續行）。
        metadata = data.get("metadata")
        final_url: str | None = None
        title = ""
        if isinstance(metadata, dict):
            raw_final_url = metadata.get("url") or metadata.get("sourceURL")
            if raw_final_url:
                final_url = str(raw_final_url)
            # 標題拿不到就留空字串；它只是 source_name 的預設值，缺了不影響抓取
            raw_title = metadata.get("title") or metadata.get("ogTitle")
            if raw_title:
                title = str(raw_title).strip()

        return ScrapedPage(text=text, final_url=final_url, title=title)

    async def scrape(self, url: str) -> str:
        return (await self.scrape_page(url)).text
