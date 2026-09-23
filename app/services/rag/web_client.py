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
    # 抓取端回報的 MIME type（Firecrawl 的 metadata.contentType）。拿不到就留
    # 空字串。用途見 resolve_page_title()：PDF 的標題要另外認。
    content_type: str = ""


# 只掃開頭幾行找標題：標題印在第一頁最上方，掃到後面撈到的是內文小節。
_TITLE_SCAN_LINES = 20
# 標題長度上限。實測最長的合法標題是台大健康電子報那種「標題＋作者＋期別」
# 的格式，約 70 字；120 留了餘裕，再長的就不是標題而是被當成一行的段落。
_MAX_TITLE_CHARS = 120


def _first_markdown_heading(text: str) -> str:
    """取開頭幾行內的第一個 markdown 標題，找不到回空字串。"""
    for line in text.splitlines()[:_TITLE_SCAN_LINES]:
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        heading = stripped.lstrip("#").strip().strip("*").strip()
        if heading and len(heading) <= _MAX_TITLE_CHARS:
            return heading
    return ""


def resolve_page_title(page: ScrapedPage) -> str:
    """決定這一頁要用的標題：PDF 以內文第一個標題優先，其餘用抓取端的標題。

    為什麼 PDF 要特別處理——PDF 的 metadata Title 是檔案屬性，機關常以舊檔
    另存新檔而沒改它。實例（2026-09-23）：疾管署的「伊波拉病毒感染 Q＆A」，
    Firecrawl 回的 title 是「中東呼吸症候群冠狀病毒感染症 Q＆A」，而內文第一
    行標題是正確的。這個標題會成為 original_title 與向量化輸入的「主題」，
    錯了等於整篇被標成另一種疾病，比沒有標題更危險。

    同一條規則也救回「PDF 完全沒有 metadata title」的情況：衛福部「平衡能力
    評估與跌倒預防」手冊的 title 是空的，收錄端因為沒有標題而整份拒收。

    HTML 不套用：<title> 是網頁作者明確寫的標題，可信；反而內文開頭的標題常
    是「跳到主要內容區塊」這類導覽文字。
    """
    if "pdf" not in (page.content_type or "").lower():
        return (page.title or "").strip()
    return _first_markdown_heading(page.text or "") or (page.title or "").strip()


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
