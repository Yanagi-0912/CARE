from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WebSearchHit:
    title: str
    url: str
    description: str = ""


@dataclass(frozen=True)
class ScrapedPage:
    """抓取結果，帶上抓取端回報的最終 URL（重導向後）。

    `final_url` 為 `None` 代表抓取端沒有回報（例如 Firecrawl 沒帶
    metadata），呼叫端需自行決定如何續行——見 design.md Decision 8。
    """

    text: str
    final_url: str | None = None


class WebSearchClient(Protocol):
    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchHit]: ...

    async def scrape(self, url: str) -> str: ...  # 保留，web_search_service.py 仍在用

    async def scrape_page(self, url: str) -> ScrapedPage: ...
