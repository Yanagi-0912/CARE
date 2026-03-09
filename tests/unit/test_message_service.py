import pytest
from unittest.mock import AsyncMock, patch
from app.services.line.message_service import LineMessageService


# 把重複的 patch 抽成 fixture，每個測試直接當參數注入即可
@pytest.fixture
def mock_send_reply():
    with patch(
        "app.services.line.message_service.LineMessageService._send_line_reply",
        new_callable=AsyncMock,  # 原本函式是非同步，所以也要用非同步 mock
        return_value=True,
    ) as m:
        yield m # yield: 把 mock 物件傳給測試函式，測試結束後會自動清理 patch


@pytest.fixture
def mock_gemini():
    with patch("app.services.line.message_service.GeminiService") as m:  # 替換 GeminiService 類別
        yield m


@pytest.mark.asyncio
async def test_process_success(mock_gemini, mock_send_reply):
    mock_gemini.return_value.generate_response = AsyncMock(return_value="AI 回覆")
    svc = LineMessageService()
    ok = await svc.process_and_reply("你好", "reply_token_xxx")

    assert ok is True
    mock_send_reply.assert_called_once()
    assert mock_send_reply.call_args[0][1] == "AI 回覆"


@pytest.mark.asyncio
async def test_process_fallback_on_value_error(mock_gemini, mock_send_reply):
    # 當 AI 丟出 ValueError 時，應送出 fallback 訊息給 LINE
    mock_gemini.return_value.generate_response = AsyncMock(
        side_effect=ValueError("API 錯誤")
    )
    svc = LineMessageService()
    ok = await svc.process_and_reply("hi", "reply_token_xxx")

    assert ok is True
    mock_send_reply.assert_called_once()
    message_sent = mock_send_reply.call_args[0][1]
    assert "抱歉" in message_sent and "API 錯誤" in message_sent
