from app.services.gemini.classification.config import (
    CLASSIFICATION_GENERATION_CONFIG,
    CLASSIFICATION_PROMPT,
)
from app.services.gemini.shared.errors import (
    GeminiNetworkError,
    GeminiHttpError,
    GeminiSchemaError,
    GeminiParseError,
    GeminiUnknownError,
)
from app.services.gemini.client.service import GeminiService
from app.services.gemini.shared.parser import parse_json_from_model_text
from app.services.gemini.shared.types import ClassificationResult
import logging

logger = logging.getLogger(__name__)

class HealthClassifier:
    def __init__(self, gemini_service: GeminiService):
        self.gemini_service = gemini_service

    async def classify(self, text: str) -> ClassificationResult:
        # 透過 Gemini 判斷使用者訊息是否為健康相關
        payload = {
            "contents": [{"parts": [{"text": f"{CLASSIFICATION_PROMPT}{text}"}]}],
            "generationConfig": CLASSIFICATION_GENERATION_CONFIG,
        }

        try:
            data = await self.gemini_service.generate_content(payload, timeout=30.0)
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            return self._parse_classification(raw_text)
        except GeminiNetworkError as e:
            logger.error(f"分類失敗（網路錯誤）: {e}")
            return ClassificationResult(is_health_related=True)
        except GeminiHttpError as e:
            logger.error(f"分類失敗（HTTP 錯誤）: {e}")
            return ClassificationResult(is_health_related=True)
        except GeminiSchemaError as e:
            logger.error(f"分類失敗（回應格式錯誤）: {e}")
            return ClassificationResult(is_health_related=True)
        except GeminiUnknownError as e:
            logger.error(f"分類失敗（未知錯誤）: {e}")
            return ClassificationResult(is_health_related=True)
        except Exception as e:
            # 分類失敗時預設當作健康訊息處理，避免誤攔使用者的正常提問
            logger.error(f"分類失敗（未處理錯誤）: {e}")
            return ClassificationResult(is_health_related=True)

    def _parse_classification(self, raw_text: str) -> ClassificationResult:
        try:
            result = parse_json_from_model_text(raw_text)
            return ClassificationResult(
                is_health_related=bool(result.get("is_health_related", True)),
            )
        except GeminiParseError as e:
            logger.warning(
                f"分類 JSON 解析失敗（解析錯誤）: {e}, 原始回應: {raw_text[:200]}"
            )
            return ClassificationResult(is_health_related=True)
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(
                f"分類 JSON 解析失敗（格式錯誤）: {e}, 原始回應: {raw_text[:200]}"
            )
            return ClassificationResult(is_health_related=True)
