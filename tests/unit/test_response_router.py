import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.gemini import GeminiResult
from app.orchestration.response_orchestrator import ResponseOrchestrator


@pytest.mark.asyncio
async def test_route_response_text_uses_gemini_directly():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock(return_value=GeminiResult(text="一般回覆"))
    guardrail_service = MagicMock()
    guardrail_service.allow_rag_tool = AsyncMock(return_value=False)
    rag_answer_service = MagicMock()

    orchestrator = ResponseOrchestrator(
        gemini_service=gemini_service,
        guardrail_service=guardrail_service,
        rag_answer_service=rag_answer_service,
    )

    result = await orchestrator.orchestrate_response("今天天氣如何")
    assert result.text == "一般回覆"


@pytest.mark.asyncio
async def test_route_response_calls_rag_tool():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock(
        return_value=GeminiResult(
            function_name="get_rag_answer",
            function_args={"query": "我有高血壓要注意什麼"},
        )
    )
    guardrail_service = MagicMock()
    guardrail_service.allow_rag_tool = AsyncMock(return_value=True)
    rag_answer_service = MagicMock()
    rag_answer_service.answer = AsyncMock(return_value="RAG 回覆")

    orchestrator = ResponseOrchestrator(
        gemini_service=gemini_service,
        guardrail_service=guardrail_service,
        rag_answer_service=rag_answer_service,
    )

    result = await orchestrator.orchestrate_response("我有高血壓要注意什麼")

    assert result.text == "RAG 回覆"


@pytest.mark.asyncio
async def test_route_response_rag_tool_fallbacks_to_gemini_when_rag_fails():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock(
        side_effect=[
            GeminiResult(function_name="get_rag_answer", function_args={"query": "高血壓"}),
            GeminiResult(text="一般回覆"),
        ]
    )
    guardrail_service = MagicMock()
    guardrail_service.allow_rag_tool = AsyncMock(return_value=True)
    rag_answer_service = MagicMock()
    rag_answer_service.answer = AsyncMock(side_effect=RuntimeError("RAG failed"))

    orchestrator = ResponseOrchestrator(
        gemini_service=gemini_service,
        guardrail_service=guardrail_service,
        rag_answer_service=rag_answer_service,
    )

    result = await orchestrator.orchestrate_response("我有高血壓要注意什麼")
    assert result.text == "一般回覆"


@pytest.mark.asyncio
async def test_route_response_non_health_disables_rag_tool():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock(return_value=GeminiResult(text="一般回覆"))
    guardrail_service = MagicMock()
    guardrail_service.allow_rag_tool = AsyncMock(return_value=False)
    rag_answer_service = MagicMock()

    orchestrator = ResponseOrchestrator(
        gemini_service=gemini_service,
        guardrail_service=guardrail_service,
        rag_answer_service=rag_answer_service,
    )

    result = await orchestrator.orchestrate_response("今天天氣如何")
    assert result.text == "一般回覆"
    gemini_service.generate_response.assert_awaited_once()
    _, kwargs = gemini_service.generate_response.await_args
    assert "tools" in kwargs
    tool_names = {
        d["name"] for d in kwargs["tools"][0]["functionDeclarations"]
    }
    assert "get_rag_answer" not in tool_names
