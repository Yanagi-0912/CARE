"""電視新聞畫面的查核工具：判定卡再加上那則新聞本身的連結。

與 `verify_claim` 的差別只有一件事——它多帶一顆「看新聞原文」。長輩拍的是
電視畫面，他想知道的除了「這是真的嗎」，還有「我看到的那則新聞在哪裡」，
而查核報告與知識庫文章都回答不了後者。

找新聞與查核**並行**：兩者互不依賴，串起來等於把搜尋的 1～2 秒直接加在長輩
的等待上。找不到就不附連結，查核卡照送（見 `tv_news_lookup` 的兩道條件）。
"""

from __future__ import annotations

import asyncio
import logging

from langchain_core.tools import tool

from app.services.media.tv_news_lookup import TvNewsArticle, TvNewsArticleFinder
from app.tools.claim_tools import claim_verification_service, render_verification

logger = logging.getLogger(__name__)

_finder: TvNewsArticleFinder | None = None


def configure_tv_news_tool(finder: TvNewsArticleFinder | None) -> None:
    """DI 初始化時注入找新聞的服務。未注入＝只做查核、不附新聞連結。"""
    global _finder
    _finder = finder


def is_tv_news_tool_configured() -> bool:
    """`verify_tv_news` 能不能提供。

    只看查核服務：找新聞是加值，沒有它這支工具仍然給得出判定卡；沒有查核服務
    則整張卡都組不出來，那時 registry 會退回提供 `verify_claim`（同樣不可用時
    再退回 `get_rag_answer`，由 nodes 的電視新聞路由決定）。
    """
    return claim_verification_service() is not None


async def _find_article(headline: str, channel: str) -> TvNewsArticle | None:
    if _finder is None or not channel:
        return None
    try:
        return await _finder.find(headline, channel)
    except Exception:  # noqa: BLE001 - 加值路徑，失敗不得影響判定卡
        logger.warning("找新聞原文失敗，只送判定卡", exc_info=True)
        return None


@tool
async def verify_tv_news(headline: str, channel: str = "") -> str:
    """當使用者傳來電視新聞畫面、系統已抽出新聞標題時呼叫，查證該標題的說法。

    `headline` 填畫面上的主標題原文（不要改寫、不要加電視台名稱），
    `channel` 填電視台名稱（例如 TVBS、民視）；認不出台別時留空。
    回傳查核判定，並在找得到時附上該則新聞的原始報導連結。
    """
    service = claim_verification_service()
    if service is None:
        return "查核判定服務未初始化，請稍後再試。"

    verify_task = asyncio.create_task(service.verify(headline))
    article_task = asyncio.create_task(_find_article(headline, channel))
    try:
        result = await verify_task
    except BaseException:
        article_task.cancel()
        raise
    article = await article_task

    logger.info(
        "stage=tv_news_verify channel=%s matched=%s article=%s",
        channel or "-",
        result.matched,
        bool(article),
    )
    return render_verification(result, article)
