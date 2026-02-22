"""
LINE Bot 消息處理服務
處理消息業務邏輯，整合 Gemini AI 服務
"""
from typing import Optional
from app.services.gemini_service import GeminiService
import logging

logger = logging.getLogger(__name__)


class LineMessageService:
    """LINE 消息處理服務"""
    
    def __init__(self):
        """初始化 LINE 消息服務和 Gemini AI"""
        self.gemini_service = GeminiService()
        logger.info("LineMessageService initialized with Gemini AI")
    
    async def process_text_message(self, user_text: str, user_id: Optional[str] = None) -> str:
        """
        處理用戶文字消息的業務邏輯，使用 Gemini AI 生成回覆
        
        Args:
            user_text: 用戶發送的文字內容
            user_id: 用戶 ID（可選，用於未來的個性化回覆）
        
        Returns:
            str: AI 生成的回覆文字內容
        """
        try:
            # 使用 Gemini AI 生成回覆
            logger.info(f"Processing message with AI for user {user_id}: {user_text[:50]}...")
            ai_response = await self.gemini_service.generate_response(user_text)
            logger.info(f"AI response generated successfully")
            return ai_response
            
        except ValueError as e:
            # 處理已知的 API 錯誤（如配額超限、網路錯誤等）
            logger.error(f"API error: {e}")
            return f"抱歉，AI 服務暫時無法使用：{str(e)}"
            
        except Exception as e:
            # 處理未預期的錯誤
            logger.error(f"Unexpected error in process_text_message: {e}", exc_info=True)
            return "抱歉，處理您的消息時發生錯誤，請稍後再試"


# 創建全局服務實例
line_message_service = LineMessageService()
