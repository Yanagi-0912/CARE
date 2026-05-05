import pytest
from unittest.mock import AsyncMock

from app.application.guardrail.service import GuardrailService
from app.infrastructure.gemini.shared.errors import (
    GeminiHttpError,
    GeminiNetworkError,
    GeminiSchemaError,
    GeminiUnknownError,
)

# Guardrail 測試只驗證「文字分類結果如何影響是否允許 RAG」，不直接測 Gemini model。


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_false_when_non_health():
    invoker = AsyncMock(return_value=False)
    guardrail = GuardrailService(async_text_to_bool=invoker)
    allowed = await guardrail.allow_rag_tool("今天天氣如何")
    assert allowed is False


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_true_when_health_related():
    invoker = AsyncMock(return_value=True)
    guardrail = GuardrailService(async_text_to_bool=invoker)
    allowed = await guardrail.allow_rag_tool("我頭痛要看哪一科")
    assert allowed is True


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_true_on_schema_error():
    invoker = AsyncMock(side_effect=GeminiSchemaError("invalid output"))
    guardrail = GuardrailService(async_text_to_bool=invoker)
    allowed = await guardrail.allow_rag_tool("我頭痛")
    assert allowed is True


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_true_on_http_error():
    invoker = AsyncMock(
        side_effect=GeminiHttpError(status_code=429, message="quota exceeded")
    )
    guardrail = GuardrailService(async_text_to_bool=invoker)
    allowed = await guardrail.allow_rag_tool("我頭痛")
    assert allowed is True


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_true_on_network_error():
    invoker = AsyncMock(side_effect=GeminiNetworkError("network down"))
    guardrail = GuardrailService(async_text_to_bool=invoker)
    allowed = await guardrail.allow_rag_tool("我頭痛")
    assert allowed is True


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_true_on_gemini_unknown_error():
    invoker = AsyncMock(side_effect=GeminiUnknownError("unexpected"))
    guardrail = GuardrailService(async_text_to_bool=invoker)
    allowed = await guardrail.allow_rag_tool("我頭痛")
    assert allowed is True


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_true_on_unknown_error():
    invoker = AsyncMock(side_effect=RuntimeError("boom"))
    guardrail = GuardrailService(async_text_to_bool=invoker)
    allowed = await guardrail.allow_rag_tool("我頭痛")
    assert allowed is True
