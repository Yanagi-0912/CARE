import httpx
from app.core.config import settings
from dataclasses import dataclass, field
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class GeminiResult:
    """
    Gemini API 的統一回傳結構。

    text 和 function_name 互斥：
      - 一般對話：text 有值，function_name 為 None
      - Function Calling：function_name 有值，text 為 None
    """

    text: Optional[str] = None
    function_name: Optional[str] = None
    function_args: dict = field(default_factory=dict)

    @property
    def is_function_call(self) -> bool:
        """回傳 True 表示 Gemini 決定呼叫工具，而非直接回答。"""
        return self.function_name is not None


# ---------------------------------------------------------------------------
# 提供給 Gemini 的工具宣告（Function Declarations）
# Gemini 會把這些 Docstring 作為決策依據，判斷何時要呼叫工具。
# ---------------------------------------------------------------------------
MEDICAL_TOOLS = [
    {
        "functionDeclarations": [
            {
                "name": "request_location",
                "description": (
                    "當用戶詢問附近的醫療院所、醫院、診所或藥局時，呼叫此工具。"
                    "此工具會要求用戶傳送目前的 GPS 位置，以便搜尋附近院所。"
                    "適用情境範例：'附近有哪些醫院'、'我要找診所'、'最近的藥局在哪'。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},  # 無需參數，user_id 由系統層提供
                },
            },
            {
                "name": "find_nearby_hospitals",
                "description": (
                    "當已取得用戶的 GPS 座標後，呼叫此工具搜尋附近的醫療院所。"
                    "通常由系統在收到用戶的位置訊息後自動呼叫，不由用戶文字觸發。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "lat": {
                            "type": "number",
                            "description": "用戶位置的緯度座標，範圍 -90.0 到 90.0。",
                        },
                        "lng": {
                            "type": "number",
                            "description": "用戶位置的經度座標，範圍 -180.0 到 180.0。",
                        },
                    },
                    "required": ["lat", "lng"],
                },
            },
        ]
    }
]


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
            async with httpx.AsyncClient(timeout=300) as client:
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
                        raise ValueError(
                            f"AI 服務發生錯誤（狀態碼: {response.status_code}）"
                        )

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

    async def generate_response_with_tools(self, user_input: str) -> GeminiResult:
        """
        呼叫 Gemini API，並啟用 Function Calling 工具。

        Gemini 若判斷需要呼叫工具（如查詢附近院所），會回傳 functionCall；
        否則回傳一般文字回覆。結果統一封裝為 GeminiResult。

        Args:
            user_input (str): 用戶輸入的文字訊息。

        Returns:
            GeminiResult:
                - 一般對話：result.text 有值，result.is_function_call 為 False
                - 工具呼叫：result.function_name 有值，result.is_function_call 為 True
        """
        payload = {
            "contents": [{"parts": [{"text": user_input}]}],
            "systemInstruction": {"parts": [{"text": self.system_instruction}]},
            "tools": MEDICAL_TOOLS,
        }

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

                # 一般文字回覆
                return GeminiResult(text=part["text"])

        except httpx.TimeoutException:
            raise ValueError("請求超時，請檢查網路連線")
        except httpx.NetworkError:
            raise ValueError("無法連線到 AI 服務，請檢查網路連線")
        except KeyError as e:
            raise ValueError(f"AI 服務回應格式異常：缺少欄位 {e}")
        except Exception as e:
            raise ValueError(f"處理請求時發生錯誤: {e}")
