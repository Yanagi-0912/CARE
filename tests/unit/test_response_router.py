import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.gemini import ClassificationResult, GeminiResult
from app.orchestration.response_router import ResponseRouter


@pytest.mark.asyncio
async def test_route_response_non_health_uses_gemini():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock(return_value=GeminiResult(text="一般回覆"))

    health_classifier = MagicMock()
    health_classifier.classify = AsyncMock(
        return_value=ClassificationResult(is_health_related=False)
    )
    rag_answer_service = MagicMock()

    router = ResponseRouter(
        gemini_service=gemini_service,
        health_classifier=health_classifier,
        rag_answer_service=rag_answer_service,
    )

    result = await router.route_response("今天天氣如何")
    assert result.text == "一般回覆"


@pytest.mark.asyncio
async def test_route_response_health_uses_rag():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock(return_value=GeminiResult(text="一般回覆"))

    health_classifier = MagicMock()
    health_classifier.classify = AsyncMock(
        return_value=ClassificationResult(is_health_related=True)
    )
    rag_answer_service = MagicMock()
    rag_answer_service.answer = AsyncMock(return_value="RAG 回覆")

    router = ResponseRouter(
        gemini_service=gemini_service,
        health_classifier=health_classifier,
        rag_answer_service=rag_answer_service,
    )

    result = await router.route_response("我有高血壓要注意什麼")

    assert result.text == "RAG 回覆"


@pytest.mark.asyncio
async def test_route_response_health_fallbacks_to_gemini_when_rag_fails():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock(return_value=GeminiResult(text="一般回覆"))

    health_classifier = MagicMock()
    health_classifier.classify = AsyncMock(
        return_value=ClassificationResult(is_health_related=True)
    )
    rag_answer_service = MagicMock()
    rag_answer_service.answer = AsyncMock(side_effect=RuntimeError("RAG failed"))

    router = ResponseRouter(
        gemini_service=gemini_service,
        health_classifier=health_classifier,
        rag_answer_service=rag_answer_service,
    )

    result = await router.route_response("我有高血壓要注意什麼")
    assert result.text == "一般回覆"
