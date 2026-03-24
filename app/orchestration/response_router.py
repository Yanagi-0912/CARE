import logging

from app.services.gemini import GeminiResult, GeminiService, HealthClassifier
from app.services.RAG.retrieval import RagAnswerService
from app.tools.registry import get_all_gemini_tools

logger = logging.getLogger(__name__)


class ResponseRouter:
    def __init__(
        self,
        gemini_service: GeminiService,
        health_classifier: HealthClassifier,
        rag_answer_service: RagAnswerService,
    ) -> None:
        self.gemini_service = gemini_service
        self.health_classifier = health_classifier
        self.rag_answer_service = rag_answer_service

    async def route_response(self, user_text: str) -> GeminiResult:
        classification = await self.health_classifier.classify(user_text)
        if classification.is_health_related:
            try:
                rag_text = await self.rag_answer_service.answer(user_text)
                return GeminiResult(text=rag_text)
            except Exception as e:
                logger.error(f"RAG 路徑失敗，改走一般 Gemini 回覆: {e}", exc_info=True)

        return await self.gemini_service.generate_response(
            user_text,
            tools=get_all_gemini_tools(),
        )
