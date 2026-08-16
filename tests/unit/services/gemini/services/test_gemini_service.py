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
