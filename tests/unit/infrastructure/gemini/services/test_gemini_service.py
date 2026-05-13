from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from app.infrastructure.gemini.services.gemini_service import GeminiService
from app.infrastructure.gemini.shared.errors import GeminiSchemaError


@pytest.fixture
def service():
    return GeminiService(api_key="dummy_key", model_name="dummy_model")


@pytest.mark.asyncio
async def test_invoke_boolean_structured_output_returns_bool(service):
    with patch(
        "app.infrastructure.gemini.services.gemini_service.ChatGoogleGenerativeAI.with_structured_output"
    ) as mock_structured:
        mock_runnable = AsyncMock()
        mock_runnable.ainvoke.return_value = True
        mock_structured.return_value = mock_runnable

        result = await service.invoke_boolean_structured_output("是不是有發燒？")
        assert result is True


@pytest.mark.asyncio
async def test_invoke_boolean_structured_output_raises_error_if_not_bool(service):
    with patch(
        "app.infrastructure.gemini.services.gemini_service.ChatGoogleGenerativeAI.with_structured_output"
    ) as mock_structured:
        mock_runnable = AsyncMock()
        mock_runnable.ainvoke.return_value = {"answer": True}  # Not a bool
        mock_structured.return_value = mock_runnable

        with pytest.raises(GeminiSchemaError):
            await service.invoke_boolean_structured_output("是不是有發燒？")
