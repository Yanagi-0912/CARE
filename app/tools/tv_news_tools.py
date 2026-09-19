"""電視新聞畫面的查核工具：判定卡再加上那則新聞本身的連結。

與 `verify_claim` 的差別只有一件事——它多帶一顆「看新聞原文」。長輩拍的是
電視畫面，他想知道的除了「這是真的嗎」，還有「我看到的那則新聞在哪裡」，
而查核報告與知識庫文章都回答不了後者。

找新聞與查核**並行**：兩者互不依賴，串起來等於把搜尋的 1～2 秒直接加在長輩
的等待上。找不到就不附連結，查核卡照送（見 `tv_news_lookup` 的兩道條件）。

### 找不到而且台標也沒認出來時，回問一句

台別是比對的關鍵（`tv_news_lookup` 的兩道條件之一），認不出來時門檻要拉高、
命中率跟著掉。與其讓長輩不知道為什麼沒有連結，不如直接問他是哪一台——他抬頭
看一眼電視就知道，比叫他重拍一張準。快速回覆的按鈕優先放他常看的台。

**只在真的沒找到時才問**：找到了還問，等於把一個已經解決的問題丟回給他。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Sequence

from langchain_core.tools import tool

from app.core.request_context import get_line_user_id
from app.services.media.tv_news_lookup import (
    CHANNEL_DOMAINS,
    TvNewsArticle,
    TvNewsArticleFinder,
)
from app.tools.claim_tools import claim_verification_service, render_verification

logger = logging.getLogger(__name__)

_finder: TvNewsArticleFinder | None = None
_channel_memory: Any = None

# 回問時快速回覆要列哪幾台。使用者常看的台排前面，不足的用收視普及的幾台補到
# 五個；全部列出十三台會讓長輩在一排小按鈕裡找字，比打字還累。
_FALLBACK_CHANNELS = ("TVBS", "三立", "東森", "民視", "中天")
_QUICK_REPLY_MAX = 5

_ASK_CHANNEL_TEXT = "另外，這則新聞是哪一台播的？告訴我，我就能幫你找到原始報導。"


def configure_tv_news_tool(
    finder: TvNewsArticleFinder | None, channel_memory: Any = None
) -> None:
    """DI 初始化時注入找新聞的服務與「常看哪幾台」的記憶。

    兩者都可以是 None：沒有 finder 就只做查核，沒有 memory 就不記也不用偏好。
    """
    global _finder, _channel_memory
    _finder = finder
    _channel_memory = channel_memory


def is_tv_news_tool_configured() -> bool:
    """`verify_tv_news` 能不能提供。

    只看查核服務：找新聞是加值，沒有它這支工具仍然給得出判定卡；沒有查核服務
    則整張卡都組不出來，那時 registry 會退回提供 `verify_claim`（同樣不可用時
    再退回 `get_rag_answer`，由 nodes 的電視新聞路由決定）。
    """
    return claim_verification_service() is not None


async def _preferred_channels() -> Sequence[str]:
    if _channel_memory is None:
        return ()
    return await _channel_memory.preferred(get_line_user_id())


async def _remember_channel(channel: str) -> None:
    """只記確定的台別：畫面認出來的，或使用者自己說的。"""
    if _channel_memory is None or channel not in CHANNEL_DOMAINS:
        return
    await _channel_memory.remember(get_line_user_id(), channel)


async def _find_article(
    headline: str, channel: str, preferred: Sequence[str]
) -> TvNewsArticle | None:
    if _finder is None:
        return None
    try:
        return await _finder.find(headline, channel, preferred)
    except Exception:  # noqa: BLE001 - 加值路徑，失敗不得影響判定卡
        logger.warning("找新聞原文失敗，只送判定卡", exc_info=True)
        return None


def _ask_channel_payload(preferred: Sequence[str]) -> dict:
    """回問台別的快速回覆。按鈕送出的字要讓 nodes 的後續路由認得出台名。"""
    channels: list[str] = []
    for channel in list(preferred) + list(_FALLBACK_CHANNELS):
        if channel in CHANNEL_DOMAINS and channel not in channels:
            channels.append(channel)
        if len(channels) >= _QUICK_REPLY_MAX:
            break
    return {
        "followUpText": _ASK_CHANNEL_TEXT,
        "quickReply": {
            "items": [
                {
                    "type": "action",
                    "action": {
                        "type": "message",
                        "label": channel,
                        "text": f"這則新聞是{channel}",
                    },
                }
                for channel in channels
            ]
        },
    }


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

    preferred = await _preferred_channels()
    verify_task = asyncio.create_task(service.verify(headline))
    article_task = asyncio.create_task(_find_article(headline, channel, preferred))
    try:
        result = await verify_task
    except BaseException:
        article_task.cancel()
        raise
    article = await article_task

    await _remember_channel(channel)

    extra: Optional[dict] = None
    if article is None and channel not in CHANNEL_DOMAINS:
        extra = _ask_channel_payload(preferred)

    logger.info(
        "stage=tv_news_verify channel=%s matched=%s article=%s ask_channel=%s",
        channel or "-",
        result.matched,
        bool(article),
        bool(extra),
    )
    rendered = render_verification(result, article, extra)
    # 退回純文字時頂層鍵沒地方放，該問的話要寫進文字裡（見 render_verification）。
    if extra is not None and not rendered.lstrip().startswith("{"):
        rendered = f"{rendered}\n\n{_ASK_CHANNEL_TEXT}"
    return rendered


@tool
async def find_tv_news_article(headline: str, channel: str) -> str:
    """使用者回答電視新聞是哪一台之後呼叫，找出那則新聞的原始報導。

    `headline` 填先前那張電視畫面的新聞標題原文，`channel` 填使用者說的
    電視台名稱。這支只找連結、不重做查核——查核結果上一則訊息已經給過了。
    """
    await _remember_channel(channel)
    article = await _find_article(headline, channel, await _preferred_channels())
    logger.info(
        "stage=tv_news_article_followup channel=%s found=%s", channel or "-", bool(article)
    )
    if article is None:
        # 說清楚是「找不到」而不是「不能給」：多數情況是電視台根本沒把這則
        # 放上網（實測 35 則裡有 20 則），長輩再拍一次也不會出現。
        return f"我找不到{channel}這則新聞的網路版，可能電視台沒有把它放上網站。"
    kind = "影片" if article.is_video else "報導"
    return f"找到了，這是{channel}的原始{kind}：\n{article.title}\n{article.url}"
