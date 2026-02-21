"""
LINE Bot 事件處理層
處理來自 LINE 平台的各種事件（消息、追蹤、取消追蹤等）
"""
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from app.core.config import settings
from app.routers.line.services import line_message_service
from app.routers.line.token_manager import line_token_manager
import logging

logger = logging.getLogger(__name__)

# 初始化 LINE SDK
handler = WebhookHandler(settings.LINE_CHANNEL_SECRET)


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event: MessageEvent):
    """
    處理文字消息事件
    
    當用戶發送文字消息時，此函數會被觸發
    
    Args:
        event: LINE MessageEvent 對象，包含用戶消息和回覆令牌
    """
    # 提取用戶消息內容
    user_text = event.message.text
    reply_token = event.reply_token
    
    # 可選：提取用戶 ID 用於未來的個性化功能
    user_id = event.source.user_id if hasattr(event.source, 'user_id') else None
    
    logger.info(f"Received message from user {user_id}: {user_text}")
    
    try:
        # 調用業務邏輯層處理消息
        # 注意：由於 WebhookHandler 的 @handler.add 裝飾器不支持異步函數，
        # 這裡直接同步調用。未來如需異步處理，可考慮使用 BackgroundTasks
        response_text = line_message_service.process_text_message(user_text, user_id)
        
        # 動態獲取 access token
        try:
            access_token = line_token_manager.get_token()
        except ValueError as e:
            logger.error(f"無法獲取 access token: {e}")
            # 如果無法獲取 token，記錄錯誤但不回覆用戶
            return
        
        # 使用動態獲取的 token 初始化 LINE Messaging API
        line_config = Configuration(access_token=access_token)
        with ApiClient(line_config) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=response_text)]
                )
            )
        
        logger.info(f"Successfully replied to user {user_id}")
        
    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)
        # 發生錯誤時，嘗試回覆錯誤消息
        # 注意：由於 reply_token 只能使用一次，這裡可能會失敗
        try:
            access_token = line_token_manager.get_token()
            line_config = Configuration(access_token=access_token)
            with ApiClient(line_config) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(text="抱歉，處理您的消息時發生錯誤，請稍後再試")]
                    )
                )
        except Exception as reply_error:
            logger.error(f"Failed to send error message: {reply_error}")


# ============================================
# 未來可擴展的事件處理器
# ============================================

# @handler.add(FollowEvent)
# def handle_follow(event):
#     """處理用戶追蹤事件"""
#     pass

# @handler.add(UnfollowEvent)
# def handle_unfollow(event):
#     """處理用戶取消追蹤事件"""
#     pass

# @handler.add(PostbackEvent)
# def handle_postback(event):
#     """處理回傳事件（按鈕點擊等）"""
#     pass
