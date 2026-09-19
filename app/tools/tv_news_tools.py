"""電視新聞畫面的查核工具：判定卡再加上那則新聞本身的連結。

與 `verify_claim` 的差別只有一件事——它多帶一顆「看新聞原文」。長輩拍的是
電視畫面，他想知道的除了「這是真的嗎」，還有「我看到的那則新聞在哪裡」，
而查核報告與知識庫文章都回答不了後者。

找新聞與查核**並行**：兩者互不依賴，串起來等於把搜尋的 1～2 秒直接加在長輩
的等待上。找不到就不附連結，查核卡照送（見 `tv_news_lookup` 的兩道條件）。

### 台標沒認出來時，先問是哪一台，問到了才查

台別是比對的關鍵（`tv_news_lookup` 的兩道條件之一），認不出來時門檻要拉高、
命中率跟著掉。2026-09-19 James 拍板：**認不出台別就先不要回答**，先問是哪一
台，等他回答再查核並找原報導——一次給完整的答案，而不是先給一張沒有連結的
判定卡、再補一則連結。

代價講清楚：長輩若沒有回答，那則謠言就完全沒有被查核。所以問句裡要附上我們
讀到的標題（他順便能看出字有沒有讀錯），按鈕要好按、優先放他常看的台。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Sequence

from langchain_core.tools import tool

from app.core.request_context import get_line_user_id
from app.services.media.tv_news_lookup import (
    CHANNEL_DOMAINS,
    TvNewsArticle,
    TvNewsArticleFinder,
)
from app.tools.claim_tools import claim_verification_service, render_verification
from resources.flex_messages import theme

logger = logging.getLogger(__name__)

_finder: TvNewsArticleFinder | None = None
_channel_memory: Any = None

# 回問時快速回覆要列哪幾台。使用者常看的台排前面，不足的用收視普及的幾台補到
# 五個；全部列出十三台會讓長輩在一排小按鈕裡找字，比打字還累。
_FALLBACK_CHANNELS = ("TVBS", "三立", "東森", "民視", "中天")
_QUICK_REPLY_MAX = 5

_ASK_CHANNEL_HEADER = "這是哪一台的新聞？"
_ASK_CHANNEL_BODY = (
    "我看不出畫面上是哪一台，點下面的電視台，我就幫你查這則新聞是真的假的，"
    "也順便找原始報導。"
)
_ASK_CHANNEL_SEEN = "我讀到的新聞標題是："


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


def _ask_channel_channels(preferred: Sequence[str]) -> list[str]:
    """按鈕要列哪幾台：常看的排前面，不足的用常見台別補到五個。"""
    channels: list[str] = []
    for channel in list(preferred) + list(_FALLBACK_CHANNELS):
        if channel in CHANNEL_DOMAINS and channel not in channels:
            channels.append(channel)
        if len(channels) >= _QUICK_REPLY_MAX:
            break
    return channels


def _ask_channel_flex(headline: str, preferred: Sequence[str]) -> str:
    """問「這是哪一台」的卡片。

    自己組 Flex 而不是回純文字：quickReply 只能掛在 Flex／文字訊息的頂層鍵上，
    而回純文字就沒有那排按鈕，長輩得自己打字。版面沿用判定卡的 header／body。
    """
    ft = theme.resolve_theme()
    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": theme.BRAND,
            "paddingAll": "lg",
            "contents": [
                {
                    "type": "text",
                    "text": _ASK_CHANNEL_HEADER,
                    "size": ft.heading,
                    "color": theme.TEXT_ON_BRAND,
                    "weight": "bold",
                    "wrap": True,
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "xl",
            "backgroundColor": theme.SURFACE,
            "spacing": "md",
            "contents": [
                {
                    "type": "text",
                    "text": _ASK_CHANNEL_BODY,
                    "size": ft.body,
                    "color": theme.TEXT,
                    "wrap": True,
                },
                # 把讀到的標題念回去：罕見字讀錯（實測「嘸效」被讀成「奏效」）
                # 只有長輩自己看得出來，而這張卡正好是他必須回應的一則。
                {
                    "type": "text",
                    "text": f"{_ASK_CHANNEL_SEEN}{headline}",
                    "size": ft.caption,
                    "color": theme.TEXT_MUTED,
                    "wrap": True,
                },
            ],
        },
    }
    payload = {
        "type": "flex",
        "altText": f"{_ASK_CHANNEL_HEADER}{headline}",
        "contents": bubble,
        "speechText": _ASK_CHANNEL_HEADER,
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
                for channel in _ask_channel_channels(preferred)
            ]
        },
    }
    return json.dumps(payload, ensure_ascii=False)


@tool
async def ask_tv_news_channel(headline: str) -> str:
    """電視新聞畫面認不出是哪一台時呼叫，先問使用者是哪一台，**先不要查核**。

    `headline` 填畫面上的主標題原文。使用者回答之後，由 `verify_tv_news`
    帶著台別查核並找原始報導。
    """
    logger.info("stage=tv_news_ask_channel")
    return _ask_channel_flex(headline, await _preferred_channels())


def _missing_note(channel: str) -> str:
    """找過、沒找到時卡片上的那一句。

    講的是「電視台沒放上網」而不是「找不到」：後者聽起來像系統壞了，前者才是
    實情——實測 35 則裡有 20 則在該台網站上根本沒有（見 tv_news_lookup）。
    """
    who = f"{channel}這則新聞" if channel else "這則新聞"
    return f"我找不到{who}的網路版，電視台不一定會把每則新聞都放上網站。"


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

    logger.info(
        "stage=tv_news_verify channel=%s matched=%s article=%s",
        channel or "-",
        result.matched,
        bool(article),
    )
    note = "" if article is not None else _missing_note(channel)
    return render_verification(result, article, news_missing_note=note)
