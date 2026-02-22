from google import genai
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

class GeminiService:
    def __init__(self):
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model_name = settings.MODEL_NAME
        self.system_instruction = (
            "你是 CARE（Clinical Assistance & Resource Engine），"
            "一個專業的健康醫療資訊 AI 助手。\n"
            "重要規則：\n"
            "1. 你必須只使用繁體中文回覆，不得使用簡體中文或其他語言\n"
            "2. 提供準確、友善且易於理解的健康醫療資訊\n"
            "3. 如遇醫療緊急情況，務必提醒用戶尋求專業醫療協助"
        )

    async def generate_response(self, user_input: str):
        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=user_input,
                config={
                    "system_instruction": self.system_instruction
                }
            )
            return response.text
        except AttributeError as e:
            error_msg = f"API 回應格式錯誤: {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            
            # 處理常見的 API 錯誤
            if "API key" in error_msg or "authentication" in error_msg.lower():
                logger.error(f"API 金鑰驗證失敗: {error_msg}")
                raise ValueError("API 金鑰無效或已過期")
            elif "quota" in error_msg.lower() or "rate limit" in error_msg.lower():
                logger.error(f"API 配額超限: {error_msg}")
                raise ValueError("API 請求配額已達上限，請稍後再試")
            elif "timeout" in error_msg.lower():
                logger.error(f"請求超時: {error_msg}")
                raise ValueError("請求超時，請檢查網路連線")
            elif "connection" in error_msg.lower():
                logger.error(f"連線錯誤: {error_msg}")
                raise ValueError("無法連線到 Gemini API，請檢查網路連線")
            else:
                logger.error(f"未預期的錯誤 ({error_type}): {error_msg}")
                raise ValueError(f"處理請求時發生錯誤: {error_msg}")