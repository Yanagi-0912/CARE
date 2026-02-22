from typing import Optional
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
)
from app.services.gemini_service import GeminiService
from app.services.line.token_manager import line_token_manager
import logging

logger = logging.getLogger(__name__)


class LineMessageService:
    def __init__(self):
        self.gemini_service = GeminiService()
        logger.info("LineMessageService initialized with Gemini AI")
    
    async def process_and_reply(self, user_text: str, reply_token: str, user_id: Optional[str] = None) -> bool:
        try:
            # 1. 生成 AI 回覆
            logger.info(f"Processing message from user {user_id}: {user_text[:50]}...")
            response_text = await self._generate_ai_response(user_text, user_id)
            
            # 2. 發送回覆到 LINE
            success = await self._send_line_reply(reply_token, response_text, user_id)
            
            if success:
                logger.info(f"Successfully processed and replied to user {user_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error in process_and_reply: {e}", exc_info=True)
            # 嘗試發送錯誤訊息
            await self._send_error_reply(reply_token, user_id)
            return False
    
    async def _generate_ai_response(self, user_text: str, user_id: Optional[str] = None) -> str:
        try:
            ai_response = await self.gemini_service.generate_response(user_text)
            logger.info(f"AI response generated for user {user_id}")
            return ai_response
            
        except ValueError as e:
            # 處理已知的 API 錯誤（如配額超限、網路錯誤等）
            logger.error(f"API error: {e}")
            return f"抱歉，AI 服務暫時無法使用：{str(e)}"
            
        except Exception as e:
            # 處理未預期的錯誤
            logger.error(f"Unexpected error in _generate_ai_response: {e}", exc_info=True)
            return "抱歉，處理您的訊息時發生錯誤，請稍後再試"
    
    async def _send_line_reply(self, reply_token: str, message_text: str, user_id: Optional[str] = None) -> bool:
        try:
            # 獲取 LINE access token
            access_token = line_token_manager.get_token()
            
            # 初始化 LINE Messaging API
            line_config = Configuration(access_token=access_token)
            with ApiClient(line_config) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=reply_token,
                        messages=[TextMessage(text=message_text)]
                    )
                )
            
            logger.info(f"Message sent to LINE for user {user_id}")
            return True
            
        except ValueError as e:
            logger.error(f"Failed to get LINE token: {e}")
            return False
            
        except Exception as e:
            logger.error(f"Failed to send LINE message: {e}", exc_info=True)
            return False
    
    async def _send_error_reply(self, reply_token: str, user_id: Optional[str] = None) -> bool:
        try:
            error_message = "抱歉，處理您的訊息時發生錯誤，請稍後再試"
            return await self._send_line_reply(reply_token, error_message, user_id)
        except Exception as e:
            logger.error(f"Failed to send error reply: {e}")
            return False

line_message_service = LineMessageService()
