"""LineEventHandler 單元測試（所有輔助函式完全整合於 handle）"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import requests
from linebot.v3.webhooks import (
    DeliveryContext,
    FileMessageContent,
    ImageMessageContent,
    MessageEvent,
    UserSource,
)

from app.services.line_messaging.event_handler import (
    LineEventHandler,
    LineValidationError,
)


def _message_event(
    message,
    *,
    reply_token: str = "dummy_token",
    user_id: str = "U12345",
) -> MessageEvent:
    return MessageEvent(
        timestamp=1,
        mode="active",
        webhookEventId="01HZTEST000000000000000000",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken=reply_token,
        source=UserSource(type="user", userId=user_id),
        message=message,
    )


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.invoke = AsyncMock(
        return_value={"response": "AI 回覆"}
    )
    return agent


@pytest.fixture
def handler(mock_agent):
    h = LineEventHandler(
        agent=mock_agent,
        channel_id="dummy_id",
        channel_secret="dummy_secret",
    )
    h.get_token = MagicMock(return_value="dummy_token")
    return h


@pytest.fixture(autouse=True)
def mock_line_api():
    """自動模擬 LINE API 連線以防止測試調用真實網路"""
    with patch("app.services.line_messaging.event_handler.Configuration") as mock_config, \
         patch("app.services.line_messaging.event_handler.ApiClient") as mock_api_client, \
         patch("app.services.line_messaging.event_handler.MessagingApi") as mock_messaging_api:
        
        messaging_api = MagicMock()
        mock_messaging_api.return_value = messaging_api
        
        yield messaging_api


@pytest.mark.asyncio
async def test_handle_media_message_infers_image(
    handler, mock_agent, mock_line_api
):
    message = FileMessageContent(
        id="M123", fileName="test.png", fileSize=100, quoteToken="dummy"
    )
    event = _message_event(message)

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process:
        await handler.handle(event)

    mock_process.assert_called_once_with(
        media_message_id="M123",
        user_media_type="image",
        source_file_name="test.png",
        user_id="U12345",
    )
    mock_agent.invoke.assert_called_once()
    agent_input = mock_agent.invoke.call_args[1]["user_input"]
    assert "image" in agent_input
    assert "[processed]" in agent_input

    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.reply_token == "dummy_token"
    assert reply_req.messages[0].text == "AI 回覆"


@pytest.mark.asyncio
async def test_handle_media_message_infers_video(
    handler, mock_agent, mock_line_api
):
    message = FileMessageContent(
        id="M124", fileName="demo.mp4", fileSize=200, quoteToken="dummy"
    )
    event = _message_event(message)

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process:
        await handler.handle(event)

    mock_process.assert_called_once_with(
        media_message_id="M124",
        user_media_type="video",
        source_file_name="demo.mp4",
        user_id="U12345",
    )


@pytest.mark.asyncio
async def test_handle_media_message_infers_audio(
    handler, mock_agent, mock_line_api
):
    message = FileMessageContent(
        id="M125", fileName="voice.mp3", fileSize=300, quoteToken="dummy"
    )
    event = _message_event(message)

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process:
        await handler.handle(event)

    mock_process.assert_called_once_with(
        media_message_id="M125",
        user_media_type="audio",
        source_file_name="voice.mp3",
        user_id="U12345",
    )


@pytest.mark.asyncio
async def test_handle_media_message_unknown_file(
    handler, mock_agent, mock_line_api
):
    message = FileMessageContent(
        id="M126", fileName="document.pdf", fileSize=400, quoteToken="dummy"
    )
    event = _message_event(message)

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process:
        await handler.handle(event)

    mock_process.assert_called_once_with(
        media_message_id="M126",
        user_media_type="file",
        source_file_name="document.pdf",
        user_id="U12345",
    )


@pytest.mark.asyncio
async def test_handle_media_message_native_image(
    handler, mock_agent, mock_line_api
):
    message = ImageMessageContent(
        id="M127",
        contentProvider={"type": "line"},
        quoteToken="qt",
    )
    event = _message_event(message)

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process:
        await handler.handle(event)

    mock_process.assert_called_once_with(
        media_message_id="M127",
        user_media_type="image",
        source_file_name=None,
        user_id="U12345",
    )


@pytest.mark.asyncio
async def test_handle_text_message_success(
    handler, mock_agent, mock_line_api
):
    from linebot.v3.webhooks import TextMessageContent

    message = TextMessageContent(id="M1", text="你好", quoteToken="dummy")
    event = _message_event(message)

    await handler.handle(event)

    mock_agent.invoke.assert_called_once()
    assert mock_agent.invoke.call_args[1]["user_input"] == "你好"
    
    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.reply_token == "dummy_token"
    assert reply_req.messages[0].text == "AI 回覆"


@pytest.mark.asyncio
async def test_handle_text_message_hospital_guide(
    handler, mock_agent, mock_line_api
):
    """
    測試使用者詢問醫院時，Agent 應回傳導引文字而非觸發工具。
    """
    from linebot.v3.webhooks import TextMessageContent

    message = TextMessageContent(id="M_HOSP", text="附近有醫院嗎", quoteToken="dummy")
    event = _message_event(message)

    guide_text = "請開啟功能選單並點擊『搜尋附近醫院』按鈕..."
    mock_agent.invoke.return_value = {
        "response": guide_text
    }

    await handler.handle(event)

    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.messages[0].text == guide_text


@pytest.mark.asyncio
async def test_handle_text_message_error_fallback(
    handler, mock_agent, mock_line_api
):
    from linebot.v3.webhooks import TextMessageContent

    message = TextMessageContent(id="M3", text="hi", quoteToken="dummy")
    event = _message_event(message)

    mock_agent.invoke.side_effect = Exception("AI Crash")

    await handler.handle(event)

    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert "發生錯誤" in reply_req.messages[0].text


@pytest.mark.asyncio
async def test_handle_location_message_delegates_to_agent(
    handler, mock_agent, mock_line_api
):
    """
    測試處理位置訊息，預期把經緯度變成字串交給 Agent。
    """
    from linebot.v3.webhooks import LocationMessageContent

    message = LocationMessageContent(
        id="M_LOC1",
        title="my location",
        address="Taipei",
        latitude=25.0330,
        longitude=121.5654,
    )
    event = _message_event(message)

    mock_agent.invoke.return_value = {
        "response": "為您找到附近醫療院所...",
    }

    await handler.handle(event)

    mock_agent.invoke.assert_called_once()
    agent_input = mock_agent.invoke.call_args[1]["user_input"]
    assert "25.033" in agent_input
    assert "121.5654" in agent_input
    
    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.messages[0].text == "為您找到附近醫療院所..."


@pytest.mark.asyncio
async def test_handle_media_message_empty_ocr_returns_error(
    handler, mock_agent, mock_line_api
):
    from linebot.v3.webhooks import ImageMessageContent

    message = ImageMessageContent(
        id="M127_empty",
        contentProvider={"type": "line"},
        quoteToken="qt",
    )
    event = _message_event(message)

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="Unable to extract text from media file (no content extracted)",
    ) as mock_process:
        await handler.handle(event)

    mock_process.assert_called_once()
    mock_agent.invoke.assert_not_called()
    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert "無法從您傳送的" in reply_req.messages[0].text


# ==============================================================================
# Token Manager 相關測試（由 LineEventHandler 擔當）
# ==============================================================================

@pytest.mark.parametrize(
    "channel_id, channel_secret",
    [
        (None, None),
        ("", ""),
    ],
)
def test_get_token_raises_when_credentials_invalid(channel_id, channel_secret):
    handler = LineEventHandler(
        agent=MagicMock(),
        channel_id=channel_id,
        channel_secret=channel_secret,
    )
    with pytest.raises(ValueError) as exc_info:
        handler.get_token()
    assert "LINE_CHANNEL_ID" in str(exc_info.value) or "LINE_CHANNEL_SECRET" in str(
        exc_info.value
    )


# ==============================================================================
# 驗證媒體訊息錯誤之整合測試（直接透過 handle）
# ==============================================================================

@pytest.mark.asyncio
async def test_handle_media_message_invalid_type(handler, mock_line_api):
    message = FileMessageContent(
        id="M123", fileName="test.invalid", fileSize=100, quoteToken="dummy"
    )
    # 模擬不支援的媒體類型
    message.type = "unsupported_media_type"
    event = _message_event(message)

    await handler.handle(event)
    
    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert "不支援的媒體類型" in reply_req.messages[0].text


@pytest.mark.asyncio
async def test_handle_media_message_invalid_filename(handler, mock_line_api):
    message = FileMessageContent(
        id="M123", fileName="   ", fileSize=100, quoteToken="dummy"
    )
    message.type = "file"
    event = _message_event(message)

    await handler.handle(event)
    
    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert "不支援的媒體類型" in reply_req.messages[0].text or "無效的媒體檔名" in reply_req.messages[0].text
