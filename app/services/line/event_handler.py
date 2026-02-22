"""
LINE Bot 事件處理層
負責接收和分發來自 LINE 平台的事件到對應的服務層
"""
from linebot.v3.webhooks import MessageEvent
from app.services.line.message_service import line_message_service
import logging

logger = logging.getLogger(__name__)


async def handle_text_message_async(event: MessageEvent):
    """
    處理文字訊息事件
    
    職責：
    1. 接收 LINE 事件
    2. 提取事件信息
    3. 分發到 message_service 處理
    
    Args:
        event: LINE MessageEvent 對象
    """
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


# ============================================
# 未來可擴展的事件處理器
# ============================================

# async def handle_follow_async(event):
#     """處理用戶追蹤事件"""
#     await line_message_service.send_welcome_message(...)

# async def handle_postback_async(event):
#     """處理按鈕點擊等互動事件"""
#     pass
