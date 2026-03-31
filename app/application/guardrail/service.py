import logging

from app.application.guardrail.config import (
    CLASSIFICATION_GENERATION_CONFIG,
    CLASSIFICATION_PROMPT,
)
from app.infrastructure.gemini import GeminiService
from app.infrastructure.gemini.shared.errors import (
    GeminiHttpError,
    GeminiNetworkError,
    GeminiParseError,
    GeminiSchemaError,
    GeminiUnknownError,
)
from app.infrastructure.gemini.shared.parser import parse_json_from_model_text

logger = logging.getLogger(__name__)

class GuardrailService:
    def __init__(self, gemini_service: GeminiService) -> None:
        self.gemini_service = gemini_service

    async def allow_rag_tool(self, user_text: str) -> bool:
        payload = {
            "contents": [{"parts": [{"text": f"{CLASSIFICATION_PROMPT}{user_text}"}]}],
            "generationConfig": CLASSIFICATION_GENERATION_CONFIG,
        }

        try:
            data = await self.gemini_service.generate_content(payload, timeout=30.0)
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = parse_json_from_model_text(raw_text)
            return bool(parsed.get("is_health_related", True))
        except GeminiParseError as e:
            logger.warning(f"Guardrail 分類解析失敗: {e}")
            return True
        except GeminiNetworkError as e:
            logger.error(f"Guardrail 分類失敗（網路錯誤）: {e}")
            return True
        except GeminiHttpError as e:
            logger.error(f"Guardrail 分類失敗（HTTP 錯誤）: {e}")
            return True
        except GeminiSchemaError as e:
            logger.error(f"Guardrail 分類失敗（回應格式錯誤）: {e}")
            return True
        except GeminiUnknownError as e:
            logger.error(f"Guardrail 分類失敗（未知錯誤）: {e}")
            return True
        except Exception as e:
            logger.error(f"Guardrail 分類失敗（未處理錯誤）: {e}")
            return True
