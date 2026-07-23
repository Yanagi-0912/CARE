import logging

from linebot.v3.webhooks import MessageEvent, LocationMessageContent
from app.services.line_messaging.handler.message_handler import BaseLineMessageHandler

logger = logging.getLogger(__name__)


class LineLocationHandler(BaseLineMessageHandler):
    """處理位置資訊訊息事件。"""

    async def handle(self, event: MessageEvent) -> None:
        message = event.message
        if not isinstance(message, LocationMessageContent):
            raise ValueError("Expected LocationMessageContent")
        user_text = f"這是我的目前位置：lat={message.latitude}, lng={message.longitude}"
        await self._process_and_reply(event, user_text, "location")
