import logging

from linebot.v3.webhooks import MessageEvent, LocationMessageContent
from app.services.line_messaging.handler.message_handler import BaseLineMessageHandler
from app.repositories.user_location_repository import UserLocationRepository

logger = logging.getLogger(__name__)
LOGGER_HEADER_TEXT = "[Handler:LocationHandler]"


class LineLocationHandler(BaseLineMessageHandler):
    """處理位置資訊訊息事件。"""

    async def handle(self, event: MessageEvent) -> None:
        message = event.message
        if not isinstance(message, LocationMessageContent):
            raise ValueError("Expected LocationMessageContent")
        #使用line內建方式取得user_id
        user_id = getattr(event.source, "user_id", None)
        if user_id:
            # 呼叫 save_location 暫存使用者位置資訊
            await UserLocationRepository.save_location(
                user_id=user_id,
                lat=message.latitude,
                lng=message.longitude,
            )
        logger.info(
            f"{LOGGER_HEADER_TEXT} 收到位置資訊，user_id=%s, lat=%s, lng=%s",
            user_id,
            message.latitude,
            message.longitude,
        )


        user_text = f"這是我的目前位置：lat={message.latitude}, lng={message.longitude}"
        logger.info(
            f"{LOGGER_HEADER_TEXT} 已轉換位置訊息為文字輸入，user_text=%s",
            user_text,
        )
        await self._process_and_reply(event, user_text, "location")
