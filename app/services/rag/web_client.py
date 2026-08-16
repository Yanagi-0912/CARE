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
    # 抓取端回報的頁面標題。內容預覽用它當新收錄 URL 的 source_name 預設值，
    # 讓庫裡本來沒有的來源也有可讀的名稱而不是空字串（design.md 決策 6 第二層）。
    # 有預設值，既有的 ScrapedPage(text=..., final_url=...) 呼叫端不受影響。
    title: str = ""


class WebSearchClient(Protocol):
    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchHit]: ...

    async def scrape(self, url: str) -> str: ...  # 保留，web_search_service.py 仍在用

    async def scrape_page(self, url: str) -> ScrapedPage: ...
