"""
LINE Bot 業務邏輯服務層
處理消息業務邏輯，未來整合 Gemini AI 服務
"""
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class LineMessageService:
    """LINE 消息處理服務"""
    
    def __init__(self):
        """初始化 LINE 消息服務"""
        logger.info("LineMessageService initialized")
    
    def process_text_message(self, user_text: str, user_id: Optional[str] = None) -> str:
        """
        處理用戶文字消息的業務邏輯
        
        Args:
            user_text: 用戶發送的文字內容
            user_id: 用戶 ID（可選，用於未來的個性化回覆）
        
        Returns:
            str: 要回覆給用戶的文字內容
        
        Note:
            目前為同步函數。當需要整合 Gemini AI 時，
            建議改為異步函數並使用 BackgroundTasks 處理
        """
        # 目前的簡單邏輯
        if user_text == "你好":
            return "你好，我是CARE，很高興為你服務"
        else:
            return "很抱歉，我目前只能回應「你好」這個訊息"
        
        # ============================================
        # TODO: 在此寫上 AI 業務邏輯
        # 未來整合 Gemini AI 服務的位置
        # 當加入 AI 時，建議將此函數改為異步：
        # 
        # async def process_text_message(...):
        #     from app.services.gemini_service import GeminiService
        #     gemini_service = GeminiService()
        #     ai_response = await gemini_service.generate_response(user_text)
        #     return ai_response
        # 
        # 同時需要修改 handlers.py 使用 BackgroundTasks 處理
        # ============================================


# 創建全局服務實例
line_message_service = LineMessageService()
