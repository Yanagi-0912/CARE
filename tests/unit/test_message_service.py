import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.line.message_service import LineMessageService
from app.services.gemini import GeminiResult


@pytest.fixture
def mock_send_reply():
    with patch(
        "app.services.line.message_service.LineMessageService.send_line_reply",
        new_callable=AsyncMock,
        return_value=True,
    ) as m:
        yield m


@pytest.mark.asyncio
async def test_process_success(mock_send_reply):
    # router 回傳一般文字（非 function call）
    mock_response_router = MagicMock()
    mock_response_router.route_response = AsyncMock(
        return_value=GeminiResult(text="AI 回覆")
    )
    svc = LineMessageService(response_router=mock_response_router)
    ok = await svc.process_and_reply("你好", "reply_token_xxx")

    assert ok is True
    mock_send_reply.assert_called_once()
    assert mock_send_reply.call_args[0][1] == "AI 回覆"


@pytest.mark.asyncio
async def test_process_function_call_request_location(mock_send_reply):
    # router 決定呼叫 request_location 工具
    with patch(
        "app.services.line.message_service.LineMessageService.send_location_quick_reply",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_quick_reply:
        mock_response_router = MagicMock()
        mock_response_router.route_response = AsyncMock(
            return_value=GeminiResult(function_name="request_location")
        )
        svc = LineMessageService(response_router=mock_response_router)
        ok = await svc.process_and_reply(
            "附近有醫院嗎", "reply_token_xxx", user_id="U123"
        )

    assert ok is True
    mock_quick_reply.assert_called_once()
    mock_send_reply.assert_not_called()  # 不應走一般文字回覆路徑


@pytest.mark.asyncio
async def test_process_fallback_on_value_error(mock_send_reply):
    # router 發生錯誤時，應送出 fallback 訊息
    mock_response_router = MagicMock()
    mock_response_router.route_response = AsyncMock(side_effect=ValueError("API 錯誤"))
    svc = LineMessageService(response_router=mock_response_router)
    ok = await svc.process_and_reply("hi", "reply_token_xxx")

    assert ok is False
    mock_send_reply.assert_called_once()
    message_sent = mock_send_reply.call_args[0][1]
    assert "抱歉" in message_sent and "API 錯誤" in message_sent


