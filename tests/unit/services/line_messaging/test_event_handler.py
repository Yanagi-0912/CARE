"""LineEventHandler 單元測試（媒體副檔名推斷與 process_media 參數）"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from linebot.v3.webhooks import (
    DeliveryContext,
    FileMessageContent,
    ImageMessageContent,
    MessageEvent,
    UserSource,
)

from app.services.line_messaging.event_handler import LineEventHandler


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
def mock_line_message_service():
    svc = MagicMock()
    svc.send_line_reply = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.invoke = AsyncMock(
        return_value={"response": "AI 回覆"}
    )
    return agent


@pytest.fixture
def handler(mock_agent, mock_line_message_service):
    return LineEventHandler(mock_agent, mock_line_message_service)


@pytest.mark.asyncio
async def test_handle_media_message_infers_image(
    handler, mock_agent, mock_line_message_service
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

    mock_line_message_service.send_line_reply.assert_called_once_with(
        "dummy_token", "AI 回覆", "U12345"
    )


@pytest.mark.asyncio
async def test_handle_media_message_infers_video(
    handler, mock_agent, mock_line_message_service
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
    handler, mock_agent, mock_line_message_service
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
    handler, mock_agent, mock_line_message_service
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
    handler, mock_agent, mock_line_message_service
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
    handler, mock_agent, mock_line_message_service
):
    from linebot.v3.webhooks import TextMessageContent

    message = TextMessageContent(id="M1", text="你好", quoteToken="dummy")
    event = _message_event(message)

    await handler.handle(event)

    mock_agent.invoke.assert_called_once_with(user_input="你好")
    mock_line_message_service.send_line_reply.assert_called_once_with(
        "dummy_token", "AI 回覆", "U12345"
    )


@pytest.mark.asyncio
async def test_handle_text_message_hospital_guide(
    handler, mock_agent, mock_line_message_service
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

    mock_line_message_service.send_line_reply.assert_called_once_with(
        "dummy_token", guide_text, "U12345"
    )


@pytest.mark.asyncio
async def test_handle_text_message_error_fallback(
    handler, mock_agent, mock_line_message_service
):
    from linebot.v3.webhooks import TextMessageContent

    message = TextMessageContent(id="M3", text="hi", quoteToken="dummy")
    event = _message_event(message)

    mock_agent.invoke.side_effect = Exception("AI Crash")

    await handler.handle(event)

    mock_line_message_service.send_line_reply.assert_called_once()
    assert "發生錯誤" in mock_line_message_service.send_line_reply.call_args[0][1]


@pytest.mark.asyncio
async def test_handle_location_message_delegates_to_agent(
    handler, mock_agent, mock_line_message_service
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
    
    mock_line_message_service.send_line_reply.assert_called_once_with(
        "dummy_token", "為您找到附近醫療院所...", "U12345"
    )
