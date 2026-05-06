import pytest
from unittest.mock import AsyncMock, MagicMock

from app.infrastructure.gemini import GeminiResult
from app.application.rag.retrieval import RagNoHitsError
from app.application.orchestration.response_orchestrator import ResponseOrchestrator


# 這邊是在測說我的流程有沒有邏輯錯誤
@pytest.mark.asyncio
async def test_route_response_text_uses_gemini_directly():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock(
        return_value=GeminiResult(text="一般回覆")
    )
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
async def test_route_response_passes_through_non_rag_function_call():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock(
        return_value=GeminiResult(
            function_name="request_location",
            function_args={},
        )
    )
    guardrail_service = MagicMock()
    guardrail_service.allow_rag_tool = AsyncMock(return_value=True)
    rag_answer_service = MagicMock()
    rag_answer_service.answer = AsyncMock()

    orchestrator = ResponseOrchestrator(
        gemini_service=gemini_service,
        guardrail_service=guardrail_service,
        rag_answer_service=rag_answer_service,
    )

    result = await orchestrator.orchestrate_response("附近哪裡有醫院")

    assert result.is_function_call is True
    assert result.function_name == "request_location"
    rag_answer_service.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_route_response_rag_no_hits_fallbacks_to_gemini_with_tools():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock(
        side_effect=[
            GeminiResult(
                function_name="get_rag_answer",
                function_args={"query": "我有高血壓要注意什麼"},
            ),
            GeminiResult(text="一般回覆"),
        ]
    )
    guardrail_service = MagicMock()
    guardrail_service.allow_rag_tool = AsyncMock(return_value=True)
    rag_answer_service = MagicMock()
    rag_answer_service.answer = AsyncMock(
        side_effect=RagNoHitsError("我有高血壓要注意什麼")
    )

    orchestrator = ResponseOrchestrator(
        gemini_service=gemini_service,
        guardrail_service=guardrail_service,
        rag_answer_service=rag_answer_service,
    )

    result = await orchestrator.orchestrate_response("我有高血壓要注意什麼")
    assert result.text == "一般回覆"
    assert gemini_service.generate_response.await_count == 2
    second_call = gemini_service.generate_response.await_args_list[1]
    assert second_call.args[0] == "我有高血壓要注意什麼"
    assert second_call.kwargs.get("tools") is not None


@pytest.mark.asyncio
async def test_route_response_rag_tool_fallbacks_to_gemini_when_rag_fails():
    gemini_service = MagicMock()
    gemini_service.generate_response = AsyncMock(
        side_effect=[
            GeminiResult(
                function_name="get_rag_answer", function_args={"query": "高血壓"}
            ),
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
    gemini_service.generate_response = AsyncMock(
        return_value=GeminiResult(text="一般回覆")
    )
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
    tool_names = {d.name for d in kwargs["tools"]}
    assert "get_rag_answer" not in tool_names
