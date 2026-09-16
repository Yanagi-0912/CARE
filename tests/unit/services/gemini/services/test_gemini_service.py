import base64
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from app.services.gemini.services.gemini_service import GeminiService
from app.services.gemini.shared.errors import GeminiSchemaError


@pytest.fixture
def service():
    return GeminiService(api_key="dummy_key", model_name="dummy_model")


@pytest.mark.asyncio
async def test_invoke_boolean_structured_output_returns_bool(service):
    with patch(
        "app.services.gemini.services.gemini_service.ChatGoogleGenerativeAI.with_structured_output"
    ) as mock_structured:
        mock_runnable = AsyncMock()
        mock_runnable.ainvoke.return_value = True
        mock_structured.return_value = mock_runnable

        result = await service.invoke_boolean_structured_output("是不是有發燒？")
        assert result is True


@pytest.mark.asyncio
async def test_invoke_boolean_structured_output_raises_error_if_not_bool(service):
    with patch(
        "app.services.gemini.services.gemini_service.ChatGoogleGenerativeAI.with_structured_output"
    ) as mock_structured:
        mock_runnable = AsyncMock()
        mock_runnable.ainvoke.return_value = {"answer": True}  # Not a bool
        mock_structured.return_value = mock_runnable

        with pytest.raises(GeminiSchemaError):
            await service.invoke_boolean_structured_output("是不是有發燒？")


@pytest.mark.asyncio
async def test_invoke_structured_output_with_image_returns_model_payload(service):
    with patch(
        "app.services.gemini.services.gemini_service.ChatGoogleGenerativeAI.with_structured_output"
    ) as mock_structured:
        mock_runnable = AsyncMock()
        mock_runnable.ainvoke.return_value = {"drugs": []}
        mock_structured.return_value = mock_runnable

        result = await service.invoke_structured_output_with_image(
            prompt="讀這張藥袋",
            image_bytes=b"raw",
            mime_type="image/png",
            json_schema={"type": "object"},
        )

        assert result == {"drugs": []}
        mock_structured.assert_called_once_with(
            {"type": "object"}, method="json_schema"
        )


@pytest.mark.asyncio
async def test_invoke_structured_output_returns_model_payload(service):
    with patch(
        "app.services.gemini.services.gemini_service.ChatGoogleGenerativeAI.with_structured_output"
    ) as mock_structured:
        mock_runnable = AsyncMock()
        mock_runnable.ainvoke.return_value = {"mentions": []}
        mock_structured.return_value = mock_runnable

        result = await service.invoke_structured_output(
            prompt="讀這段文字",
            json_schema={"type": "object"},
        )

        assert result == {"mentions": []}
        mock_structured.assert_called_once_with(
            {"type": "object"}, method="json_schema"
        )
        (messages,), _ = mock_runnable.ainvoke.call_args
        assert messages[0].content == "讀這段文字"


@pytest.mark.asyncio
async def test_invoke_structured_output_with_image_sends_prompt_and_inline_image(service):
    with patch(
        "app.services.gemini.services.gemini_service.ChatGoogleGenerativeAI.with_structured_output"
    ) as mock_structured:
        mock_runnable = AsyncMock()
        mock_runnable.ainvoke.return_value = {}
        mock_structured.return_value = mock_runnable

        await service.invoke_structured_output_with_image(
            prompt="讀這張藥袋",
            image_bytes=b"raw",
            mime_type="image/png",
            json_schema={"type": "object"},
        )

        (messages,), _ = mock_runnable.ainvoke.call_args
        content = messages[0].content
        assert content[0] == {"type": "text", "text": "讀這張藥袋"}
        assert content[1]["type"] == "image_url"
        # 影像以 data URI 內嵌，不落地成檔案也不對外產生可讀取的網址
        assert content[1]["image_url"].startswith("data:image/png;base64,")
        assert base64.b64decode(content[1]["image_url"].split(",", 1)[1]) == b"raw"


def test_thinking_level_is_passed_to_chat_model():
    svc = GeminiService(
        api_key="dummy_key", model_name="dummy_model", thinking_level="low"
    )
    assert svc.chat_model.thinking_level == "low"


def test_thinking_level_defaults_to_model_default():
    svc = GeminiService(api_key="dummy_key", model_name="dummy_model")
    assert svc.chat_model.thinking_level is None


# --- 逾時與重試次數集中在這裡設（2026-09-16） -----------------------------------

from app.core.config import settings
from app.services.gemini.services.gemini_service import DEFAULT_MAX_RETRIES


def test_chat_model_gets_explicit_timeout_and_retries_by_default():
    """langchain-google-genai 預設 timeout=None、重試 6 次；每個實例都要蓋掉。"""
    svc = GeminiService(api_key="dummy_key", model_name="dummy_model")
    assert svc.chat_model.timeout == settings.GEMINI_REQUEST_TIMEOUT_SECONDS
    assert svc.chat_model.max_retries == DEFAULT_MAX_RETRIES == 2
    assert svc.timeout == settings.GEMINI_REQUEST_TIMEOUT_SECONDS
    assert svc.max_retries == 2


def test_timeout_and_retries_can_be_overridden():
    svc = GeminiService(
        api_key="dummy_key", model_name="dummy_model", timeout=5, max_retries=0
    )
    assert svc.chat_model.timeout == 5.0
    assert svc.chat_model.max_retries == 0


def test_request_timeout_stays_below_rag_total_budget():
    """單次逾時要小於 RAG 總預算，一次卡住的連線才不可能獨自吃掉整條管線。"""
    assert 0 < settings.GEMINI_REQUEST_TIMEOUT_SECONDS < settings.RAG_ANSWER_TIMEOUT_SECONDS
