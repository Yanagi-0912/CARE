import httpx
from app.core.config import settings
from app.services.gemini.types import GeminiResult
from app.services.gemini.validation import validate_user_input
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

    async def generate_response(self, user_input: str, tools: list = None) -> GeminiResult:
        validation = validate_user_input(user_input)
        if not validation.is_valid:
            return GeminiResult(text=validation.error_message)

        payload = {
            "contents": [{"parts": [{"text": user_input}]}],
            "systemInstruction": {"parts": [{"text": self.system_instruction}]},
        }

        if tools:
            payload["tools"] = tools

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                logger.info(
                    f"Sending tool-enabled request to Gemini: {user_input[:50]}..."
                )

                response = await client.post(
                    self.api_url,
                    params={"key": self.api_key},
                    json=payload,
                )

                if response.status_code != 200:
                    logger.error(f"Gemini API error: {response.status_code}")
                    raise ValueError(
                        f"AI 服務發生錯誤（狀態碼: {response.status_code}）"
                    )

                data = response.json()
                part = data["candidates"][0]["content"]["parts"][0]

                # Gemini 回傳 functionCall 時，part 內有 "functionCall" 欄位
                if "functionCall" in part:
                    func = part["functionCall"]
                    logger.info(f"Gemini requested function call: {func['name']}")
                    return GeminiResult(
                        function_name=func["name"],
                        function_args=func.get("args", {}),
                    )

                return GeminiResult(text=part["text"])

        except httpx.TimeoutException:
            raise ValueError("請求超時，請檢查網路連線")
        except httpx.NetworkError:
            raise ValueError("無法連線到 AI 服務，請檢查網路連線")
        except KeyError as e:
            raise ValueError(f"AI 服務回應格式異常：缺少欄位 {e}")
        except Exception as e:
            raise ValueError(f"處理請求時發生錯誤: {e}")
