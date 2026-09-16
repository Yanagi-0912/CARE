from __future__ import annotations

from collections.abc import Sequence
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


class WebSearchUnavailable(Exception):
    """搜尋服務沒有給出結果——是「沒搜成」，不是「找不到」。

    兩者以前被混成同一個空 list：Firecrawl 回 429（免費方案每分鐘的搜尋額度
    用完）、逾時、5xx，全部變成 0 筆，使用者看到的是「找不到，請換個方式描述」。
    換十種說法都一樣，因為根本沒搜。分開之後呼叫端才能決定要不要重搜（限流時
    馬上重搜只會再吃一次 429）、要對使用者說什麼。

    `status` 是 HTTP 狀態碼，非 HTTP 類的失敗（逾時、連不上）為 None。
    """

    def __init__(self, reason: str, *, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status

    @property
    def rate_limited(self) -> bool:
        return self.status == 429


class WebSearchClient(Protocol):
    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        include_domains: Sequence[str] | None = None,
    ) -> list[WebSearchHit]:
        """回傳命中；空 list 只代表真的沒找到，搜尋本身失敗要拋 `WebSearchUnavailable`。"""
        ...

    async def scrape(self, url: str) -> str: ...  # 保留，web_search_service.py 仍在用

    async def scrape_page(self, url: str) -> ScrapedPage: ...
