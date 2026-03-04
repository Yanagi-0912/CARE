from unittest.mock import AsyncMock, patch

import pytest

from app.services.line.message_service import LineMessageService
#patch 在跑測試時候把某個東西替換成假的，如我不替換單元測試就會去真的呼叫 geminiapi 或者 lineapi
#patch 是檢查邏輯用的
@patch(
    "app.services.line.message_service.LineMessageService._send_line_reply",
    new_callable=AsyncMock,#原本含式是非同步所以也要用非同步
    return_value=True,#每次呼叫這個假含式是回傳True
)
@patch("app.services.line.message_service.GeminiService")#這邊替換geminiService類別 
@pytest.mark.asyncio
async def test_process_success(mock_gemini, mock_send_reply):
    mock_gemini.return_value.generate_response = AsyncMock(return_value="AI 回覆")
    svc = LineMessageService()
    ok = await svc.process_and_reply("你好", "reply_token_xxx")

    assert ok is True
    mock_send_reply.assert_called_once()
    called_args = mock_send_reply.call_args[0]
    assert called_args[1] == "AI 回覆"

#patch在每個測試都要有自己的patch 不會共用，pytest跟patch 基本用法
@patch(
    "app.services.line.message_service.LineMessageService._send_line_reply",
    new_callable=AsyncMock,
    return_value=True,
)
@patch("app.services.line.message_service.GeminiService")
@pytest.mark.asyncio
async def test_process_fallback_on_value_error(mock_gemini, mock_send_reply):#當ai丟出value error 時候，應該送出fallback 訊息給 LINE
    mock_gemini.return_value.generate_response = AsyncMock(
        side_effect=ValueError("API 錯誤")#假設ai 回api錯誤
    )
    svc = LineMessageService()
    ok = await svc.process_and_reply("hi", "reply_token_xxx")

    assert ok is True
    mock_send_reply.assert_called_once()
    message_sent = mock_send_reply.call_args[0][1]
    assert "抱歉" in message_sent and "API 錯誤" in message_sent#確定有送出fallback 訊息給 LINE
