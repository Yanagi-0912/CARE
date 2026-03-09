from linebot.v3.webhooks import MessageEvent
from app.services.line.message_service import line_message_service
import logging

logger = logging.getLogger(__name__)


async def handle_text_message_async(event: MessageEvent):
    # 提取事件信息
    user_text = event.message.text
    reply_token = event.reply_token
    user_id = event.source.user_id if hasattr(event.source, 'user_id') else None
    
    logger.info(f"Received text message event from user {user_id}")
    
    # 委派給 message_service 處理完整流程
    await line_message_service.process_and_reply(
        user_text=user_text,
        reply_token=reply_token,
        user_id=user_id
    )

# 在未來如果需要處理其他類型的事件（如圖片、影片等），可以在這裡添加相應的處理函數。