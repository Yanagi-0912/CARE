"""分享 CARE 的 AI 工具。

常見說法在 message handler 就被關鍵字攔下、不會走到這裡（見 share_intent）；
這個工具接的是換了講法的要求（「怎麼讓我朋友也用這個」），由模型判斷後呼叫，
回的是同一張分享卡。
"""

import logging
from typing import Optional

from langchain_core.tools import tool

from app.i18n.messages import t
from app.services.line_messaging.share_card import ShareCardService

logger = logging.getLogger(__name__)

_share_card_service: Optional[ShareCardService] = None


def configure_share_tool(service: Optional[ShareCardService]) -> None:
    """DI 初始化時呼叫，注入分享卡服務。"""
    global _share_card_service
    _share_card_service = service


@tool
async def share_care() -> str:
    """當使用者想把 CARE 分享或推薦給朋友、要 CARE 的加好友連結或 QR code，或想邀請家人加入他的家庭時呼叫此工具。"""
    if _share_card_service is None:
        logger.warning("share_care called but the share card service is not configured")
        return t("share.unavailable")
    return await _share_card_service.build_reply_text()
