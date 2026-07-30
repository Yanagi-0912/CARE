"""處理查看院所詳細資訊的 Postback 事件。

使用者於候選清單卡片點擊"查看詳情"後，LINE 會送出帶有 facility_id 的
postback 事件，此 handler 直接查詢資料庫並組出 Flex Message 回覆，
不經過 AI agent 判斷，屬於結構化操作的程式邏輯保底處理。
"""

import json
import logging

from app.services.medical.medical_service import MedicalService
from app.services.line_messaging.reply.reply import LineReplier
from resources.flex_messages.medical_messages.facility_detail_flex_message import (
    generate_facility_detail_flex_message,
)

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Handler:FacilityDetailHandler]"

NO_FACILITY_DETAIL_MESSAGE = "查無此院所資料，可能已被更新或移除。"


class LineFacilityDetailHandler:
    """處理院所詳情查詢的 Postback 事件，直接查資料庫並回覆 Flex Message。"""

    def __init__(self, medical_service: MedicalService, replier: LineReplier):
        self._medical_service = medical_service
        self._replier = replier

    async def handle_view_facility_detail(
        self, facility_id: str, reply_token: str, user_id: str
    ) -> None:
        logger.info(
            f"{LOGGER_HEADER_TEXT} 收到院所詳情查詢請求，facility_id=%s, user_id=%s",
            facility_id,
            user_id,
        )
        #取得單一院所資料，若查無則回覆查無資料訊息
        facility = await self._medical_service.get_facility_by_id(facility_id)

        if facility is None:
            logger.info(
                f"{LOGGER_HEADER_TEXT} 查無院所資料，facility_id=%s", facility_id
            )
            await self._replier.reply(
                reply_token=reply_token,
                message_text=NO_FACILITY_DETAIL_MESSAGE,
                user_id=user_id,
                voice_reply_enabled=False,
            )
            return

        flex_payload = generate_facility_detail_flex_message(facility)
        message_text = json.dumps(flex_payload, ensure_ascii=False)
        await self._replier.reply(
            reply_token=reply_token,
            message_text=message_text,
            user_id=user_id,
            voice_reply_enabled=False,
        )
        logger.info(
            f"{LOGGER_HEADER_TEXT} 已回覆院所詳情，facility_id=%s", facility_id
        )