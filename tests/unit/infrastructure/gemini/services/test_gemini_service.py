from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from app.infrastructure.gemini import GeminiHttpError, GeminiService


def _svc_with_mock_chat(mock_llm: MagicMock) -> GeminiService:
    with patch(
        "app.infrastructure.gemini.services.gemini_service.ChatGoogleGenerativeAI",
        return_value=mock_llm,
    ):
        return GeminiService(api_key="test_key", model_name="gemini-2.0-flash")


@pytest.mark.asyncio
async def test_generate_response_returns_text_on_success():
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="AI 回覆內容"))

    svc = _svc_with_mock_chat(mock_llm)
    result = await svc.generate_response("你好")

    assert result.text == "AI 回覆內容"
    mock_llm.ainvoke.assert_awaited()


@pytest.mark.asyncio
async def test_generate_response_handles_list_content_blocks():
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(
            content=[
                {"type": "text", "text": "第一段"},
                {"type": "text", "text": "第二段"},
            ]
        )
    )

    svc = _svc_with_mock_chat(mock_llm)
    result = await svc.generate_response("你好")

    assert result.text == [
        {"type": "text", "text": "第一段"},
        {"type": "text", "text": "第二段"},
    ]


@pytest.mark.asyncio
async def test_generate_response_returns_validation_error_without_api_call():
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock()

    svc = _svc_with_mock_chat(mock_llm)
    result = await svc.generate_response("   ")

    assert result.text == "請輸入訊息內容，不能為空白。"
    assert result.is_function_call is False
    mock_llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_response_returns_function_call_when_tools_bound():
    mock_llm = MagicMock()
    bound = MagicMock()
    bound.ainvoke = AsyncMock(
        return_value=AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "request_location",
                    "args": {},
                    "id": "call_1",
                    "type": "tool_call",
                }
            ],
        )
    )
    mock_llm.bind_tools = MagicMock(return_value=bound)

    svc = _svc_with_mock_chat(mock_llm)
    tools = [
        {
            "name": "request_location",
            "parameters": {"type": "object", "properties": {}},
        }
    ]
    result = await svc.generate_response("附近哪裡有醫院", tools=tools)

    assert result.is_function_call is True
    assert result.function_name == "request_location"
    assert result.function_args == {}
    mock_llm.bind_tools.assert_called_once()


@pytest.mark.asyncio
async def test_generate_response_raises_http_error_on_429():
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        side_effect=GeminiHttpError(429, "AI 服務發生錯誤（狀態碼: 429）")
    )

    svc = _svc_with_mock_chat(mock_llm)

    with pytest.raises(GeminiHttpError) as exc_info:
        await svc.generate_response("hi")

    assert "429" in str(exc_info.value) or exc_info.value.status_code == 429
