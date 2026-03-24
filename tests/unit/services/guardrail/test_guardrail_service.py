import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.gemini.shared.errors import (
    GeminiHttpError,
    GeminiNetworkError,
    GeminiParseError,
    GeminiSchemaError,
    GeminiUnknownError,
)
from app.services.guardrail.service import GuardrailService


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_false_when_non_health():
    gemini_service = MagicMock()
    gemini_service.generate_content = AsyncMock(
        return_value={
            "candidates": [
                {"content": {"parts": [{"text": '{"is_health_related": false}'}]}}
            ]
        }
    )
    guardrail = GuardrailService(gemini_service=gemini_service)

    allowed = await guardrail.allow_rag_tool("今天天氣如何")
    assert allowed is False


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_true_when_health_related():
    gemini_service = MagicMock()
    gemini_service.generate_content = AsyncMock(
        return_value={
            "candidates": [
                {"content": {"parts": [{"text": '{"is_health_related": true}'}]}}
            ]
        }
    )
    guardrail = GuardrailService(gemini_service=gemini_service)

    allowed = await guardrail.allow_rag_tool("我頭痛要看哪一科")
    assert allowed is True


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_true_on_parse_error():
    gemini_service = MagicMock()
    gemini_service.generate_content = AsyncMock(
        side_effect=GeminiParseError("invalid json")
    )
    guardrail = GuardrailService(gemini_service=gemini_service)

    allowed = await guardrail.allow_rag_tool("我頭痛")
    assert allowed is True


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_true_on_http_error():
    gemini_service = MagicMock()
    gemini_service.generate_content = AsyncMock(
        side_effect=GeminiHttpError(status_code=429, message="quota exceeded")
    )
    guardrail = GuardrailService(gemini_service=gemini_service)

    allowed = await guardrail.allow_rag_tool("我頭痛")
    assert allowed is True


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_true_on_network_error():
    gemini_service = MagicMock()
    gemini_service.generate_content = AsyncMock(
        side_effect=GeminiNetworkError("network down")
    )
    guardrail = GuardrailService(gemini_service=gemini_service)

    allowed = await guardrail.allow_rag_tool("我頭痛")
    assert allowed is True


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_true_on_schema_error():
    gemini_service = MagicMock()
    gemini_service.generate_content = AsyncMock(
        side_effect=GeminiSchemaError("missing field")
    )
    guardrail = GuardrailService(gemini_service=gemini_service)

    allowed = await guardrail.allow_rag_tool("我頭痛")
    assert allowed is True


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_true_on_gemini_unknown_error():
    gemini_service = MagicMock()
    gemini_service.generate_content = AsyncMock(
        side_effect=GeminiUnknownError("unexpected")
    )
    guardrail = GuardrailService(gemini_service=gemini_service)

    allowed = await guardrail.allow_rag_tool("我頭痛")
    assert allowed is True


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_true_on_unknown_error():
    gemini_service = MagicMock()
    gemini_service.generate_content = AsyncMock(side_effect=RuntimeError("boom"))
    guardrail = GuardrailService(gemini_service=gemini_service)

    allowed = await guardrail.allow_rag_tool("我頭痛")
    assert allowed is True
