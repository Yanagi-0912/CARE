import httpx
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


class GeminiService:
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.MODEL_NAME
        self.api_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_name}:generateContent"
        )
        self.system_instruction = (
            "你是 CARE（Clinical Assistance & Resource Engine），"
            "一個專業的健康醫療資訊 AI 助手。\n"
            "重要規則：\n"
            "1. 你必須只使用繁體中文回覆，不得使用簡體中文或其他語言\n"
            "2. 提供準確、友善且易於理解的健康醫療資訊\n"
            "3. 如遇醫療緊急情況，務必提醒用戶尋求專業醫療協助"
        )
        logger.info(f"GeminiService initialized with model: {self.model_name}")

    async def generate_response(self, user_input: str) -> str:

        payload = {
            "contents": [{"parts": [{"text": user_input}]}],
            "systemInstruction": {"parts": [{"text": self.system_instruction}]},
        }
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                logger.info(f"Sending request to Gemini API: {user_input[:50]}...")
                
                response = await client.post(
                    self.api_url,
                    params={"key": self.api_key},
                    json=payload,
                )
                
                # 檢查 HTTP 狀態碼
                if response.status_code != 200:
                    logger.error(
                        f"Gemini API error: Status {response.status_code}, "
                        f"Response: {response.text}"
                    )
                    
                    # 錯誤訊息
                    if response.status_code == 400:
                        raise ValueError("請求格式錯誤，請稍後再試")
                    elif response.status_code == 401:
                        raise ValueError("API 金鑰無效或已過期")
                    elif response.status_code == 403:
                        raise ValueError("API 權限不足，請檢查金鑰設定")
                    elif response.status_code == 429:
                        raise ValueError("API 請求配額已達上限，請稍後再試")
                    elif response.status_code == 500:
                        raise ValueError("AI 服務暫時無法使用，請稍後再試")
                    else:
                        raise ValueError(f"AI 服務發生錯誤（狀態碼: {response.status_code}）")
                
                # 解析回應
                data = response.json()
                ai_response = data["candidates"][0]["content"]["parts"][0]["text"]
                
                logger.info("Successfully received AI response")
                return ai_response
                
        except httpx.TimeoutException:
            error_msg = "請求超時，請檢查網路連線"
            logger.error(f"Timeout error: {error_msg}")
            raise ValueError(error_msg)
            
        except httpx.NetworkError as e:
            error_msg = f"網路連線錯誤: {str(e)}"
            logger.error(f"Network error: {error_msg}")
            raise ValueError("無法連線到 AI 服務，請檢查網路連線")
            
        except KeyError as e:
            error_msg = f"API 回應格式錯誤: 缺少欄位 {str(e)}"
            logger.error(f"Response parsing error: {error_msg}")
            raise ValueError("AI 服務回應格式異常，請稍後再試")
            
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            logger.error(f"Unexpected error ({error_type}): {error_msg}", exc_info=True)
            raise ValueError(f"處理請求時發生錯誤: {error_msg}")