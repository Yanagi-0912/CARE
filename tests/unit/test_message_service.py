"""LineMessageService 單元測試：mock Gemini 與 LINE API."""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.line.message_service import LineMessageService


@patch("app.services.line.message_service.LineMessageService._send_line_reply", new_callable=AsyncMock, return_value=True)
@patch("app.services.line.message_service.GeminiService")
@pytest.mark.asyncio
async def test_process_and_reply_success(mock_gemini_class, mock_send):
    mock_gemini_class.return_value.generate_response = AsyncMock(return_value="AI 回覆")
    service = LineMessageService()
    result = await service.process_and_reply("你好", "reply_token_xxx")
    assert result is True
    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[1] == "AI 回覆"


@patch("app.services.line.message_service.LineMessageService._send_line_reply", new_callable=AsyncMock, return_value=True)
@patch("app.services.line.message_service.GeminiService")
@pytest.mark.asyncio
async def test_process_and_reply_sends_fallback_when_ai_raises_value_error(mock_gemini_class, mock_send):
    """AI 拋 ValueError 時，_generate_ai_response 會捕獲並回傳 fallback 字串，再送給 LINE."""
    mock_gemini_class.return_value.generate_response = AsyncMock(side_effect=ValueError("API 錯誤"))
    service = LineMessageService()
    result = await service.process_and_reply("hi", "reply_token_xxx")
    assert result is True
    mock_send.assert_called_once()
    message_sent = mock_send.call_args[0][1]
    assert "抱歉" in message_sent and "API 錯誤" in message_sent
