import json
import httpx
from app.core.config import settings
from app.services.gemini.types import ClassificationResult
import logging

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = (
    "你是一個訊息分類器。請判斷以下使用者訊息是否與「健康、醫療、身體狀況、疾病、藥物、營養、運動健身、心理健康」相關。\n\n"
    "請嚴格以 JSON 格式回覆，不要包含其他文字：\n"
    '{"is_health_related": true/false, "confidence": 0.0~1.0, "category": "分類名稱或null"}\n\n'
    "分類名稱範例：疾病症狀、藥物諮詢、營養飲食、運動健身、心理健康、醫療資源、一般健康\n\n"
    "使用者訊息：\n"
)


class HealthClassifier:
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.model_name = settings.MODEL_NAME
        self.api_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_name}:generateContent"
        )

    async def classify(self, text: str) -> ClassificationResult:
        # 透過 Gemini 判斷使用者訊息是否為健康相關
        payload = {
            "contents": [{"parts": [{"text": f"{CLASSIFICATION_PROMPT}{text}"}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 200,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.api_url,
                    params={"key": self.api_key},
                    json=payload,
                )

                if response.status_code != 200:
                    logger.error(f"Classification API error: {response.status_code}")
                    return ClassificationResult(is_health_related=True, confidence=0.0)

                data = response.json()
                raw_text = data["candidates"][0]["content"]["parts"][0]["text"]

                return self._parse_classification(raw_text)

        except Exception as e:
            # 分類失敗時預設當作健康訊息處理，避免誤攔使用者的正常提問
            logger.error(f"Classification failed, defaulting to health-related: {e}")
            return ClassificationResult(is_health_related=True, confidence=0.0)

    def _parse_classification(self, raw_text: str) -> ClassificationResult:
        # 從 Gemini 回覆中解析 JSON 分類結果
        try:
            cleaned = raw_text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            result = json.loads(cleaned)
            return ClassificationResult(
                is_health_related=bool(result.get("is_health_related", True)),
                confidence=float(result.get("confidence", 0.0)),
                category=result.get("category"),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse classification JSON: {e}, raw: {raw_text[:200]}")
            return ClassificationResult(is_health_related=True, confidence=0.0)
