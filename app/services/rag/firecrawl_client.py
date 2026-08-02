from __future__ import annotations

import logging

import httpx

from app.services.rag.web_client import WebSearchHit

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

    async def scrape(self, url: str) -> str:
        if not self._api_key:
            return ""
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
            return ""
        except Exception:
            logger.exception("Firecrawl scrape failed url=%s", url)
            return ""
        finally:
            if owns_client:
                await client.aclose()

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return ""
        return str(data.get("markdown") or "").strip()
