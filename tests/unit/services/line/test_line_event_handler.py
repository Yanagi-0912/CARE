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

from app.services.line.event_handler import LineEventHandler


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
    svc.process_and_reply = AsyncMock()
    svc.send_line_reply = AsyncMock()
    return svc


@pytest.fixture
def mock_medical_service():
    return MagicMock()


@pytest.fixture
def handler(mock_line_message_service, mock_medical_service):
    return LineEventHandler(mock_line_message_service, mock_medical_service)


@pytest.mark.asyncio
async def test_handle_media_message_infers_image(handler, mock_line_message_service):
    message = FileMessageContent(id="M123", fileName="test.png", fileSize=100)
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
    mock_line_message_service.process_and_reply.assert_called_once()
    call_kw = mock_line_message_service.process_and_reply.call_args.kwargs
    assert call_kw["reply_token"] == "dummy_token"
    assert call_kw["user_id"] == "U12345"
    assert "image" in call_kw["user_text"]
    assert "[processed]" in call_kw["user_text"]


@pytest.mark.asyncio
async def test_handle_media_message_infers_video(handler, mock_line_message_service):
    message = FileMessageContent(id="M124", fileName="demo.mp4", fileSize=200)
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
async def test_handle_media_message_infers_audio(handler, mock_line_message_service):
    message = FileMessageContent(id="M125", fileName="voice.mp3", fileSize=300)
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
async def test_handle_media_message_unknown_file(handler, mock_line_message_service):
    message = FileMessageContent(id="M126", fileName="document.pdf", fileSize=400)
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
async def test_handle_media_message_native_image(handler, mock_line_message_service):
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
