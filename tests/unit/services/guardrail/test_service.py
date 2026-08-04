import pytest
from unittest.mock import AsyncMock

from app.services.guardrail import service as guardrail_module
from app.services.guardrail.service import GuardrailService


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_false_when_non_health():
    invoker = AsyncMock(return_value=False)
    guardrail = GuardrailService(async_text_to_bool=invoker)
    allowed = await guardrail.allow_rag_tool("今天天氣如何")
    assert allowed is False
    invoker.assert_awaited_once()


@pytest.mark.asyncio
async def test_allow_rag_tool_returns_true_when_health_related():
    invoker = AsyncMock(return_value=True)
    guardrail = GuardrailService(async_text_to_bool=invoker)
    allowed = await guardrail.allow_rag_tool("我頭痛要看哪一科")
    assert allowed is True


@pytest.mark.asyncio
async def test_allow_rag_tool_skips_classifier_for_location_message():
    invoker = AsyncMock(return_value=True)
    guardrail = GuardrailService(async_text_to_bool=invoker)
    allowed = await guardrail.allow_rag_tool(
        "這是我的目前位置：lat=25.0, lng=121.5"
    )
    assert allowed is False
    invoker.assert_not_awaited()


def test_classification_prompt_covers_medical_fraud():
    prompt = guardrail_module._CLASSIFICATION_PROMPT
    assert "假藥" in prompt or "詐騙" in prompt
    assert "醫療" in prompt


@pytest.mark.asyncio
async def test_allow_rag_tool_classifies_medical_fraud_message():
    invoker = AsyncMock(return_value=True)
    guardrail = GuardrailService(async_text_to_bool=invoker)
    allowed = await guardrail.allow_rag_tool("收到藥局簡訊要我先轉帳才能領藥")
    assert allowed is True
    invoker.assert_awaited_once()


@pytest.mark.asyncio
async def test_allow_rag_tool_fail_open_on_error():
    invoker = AsyncMock(side_effect=RuntimeError("boom"))
    guardrail = GuardrailService(async_text_to_bool=invoker)
    allowed = await guardrail.allow_rag_tool("我頭痛")
    assert allowed is True
