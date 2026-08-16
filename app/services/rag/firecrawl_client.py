from __future__ import annotations

import logging

import httpx

from app.services.rag.web_client import ScrapedPage, WebSearchHit

logger = logging.getLogger(__name__)


class FirecrawlClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.firecrawl.dev/v1",
        timeout_seconds: float = 15.0,
        scrape_timeout_seconds: float | None = None,
        http_client: httpx.AsyncClient | None = None,
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

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchHit]:
        if not self._api_key:
            return []
        client = self._http_client or httpx.AsyncClient(timeout=self._timeout_seconds)
        owns_client = self._http_client is None
        try:
            response = await client.post(
                f"{self._base_url}/search",
                headers=self._headers(),
                json={"query": query, "limit": limit},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            logger.exception("Firecrawl search failed")
            return []
        finally:
            if owns_client:
                await client.aclose()

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return []
        hits: list[WebSearchHit] = []
        for item in data:
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
