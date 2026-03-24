import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from linebot.v3.webhooks import (
    MessageEvent,
    FileMessageContent,
    ImageMessageContent,
)
from app.services.line.event_handler import LineEventContext


@pytest.fixture
def mock_event():
    event = MagicMock(spec=MessageEvent)
    event.reply_token = "dummy_token"
    source = MagicMock()
    source.user_id = "U12345"
    event.source = source
    return event


def _mock_line_service():
    svc = MagicMock()
    svc.process_and_reply = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_handle_media_message_infers_image(mock_event):
    message = MagicMock(spec=FileMessageContent)
    message.id = "M123"
    message.type = "file"
    message.file_name = "test.png"
    mock_event.message = message

    context = LineEventContext(mock_event)

    with patch(
        "app.services.line.event_handler.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process, patch(
        "app.services.line.event_handler.get_line_message_service",
        return_value=_mock_line_service(),
    ):
        await context.handle_media_message()

    mock_process.assert_called_once_with(
        media_message_id="M123",
        user_media_type="image",
        source_file_name="test.png",
        user_id="U12345",
    )


@pytest.mark.asyncio
async def test_handle_media_message_infers_video(mock_event):
    message = MagicMock(spec=FileMessageContent)
    message.id = "M124"
    message.type = "file"
    message.file_name = "demo.mp4"
    mock_event.message = message

    context = LineEventContext(mock_event)

    with patch(
        "app.services.line.event_handler.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process, patch(
        "app.services.line.event_handler.get_line_message_service",
        return_value=_mock_line_service(),
    ):
        await context.handle_media_message()

    mock_process.assert_called_once_with(
        media_message_id="M124",
        user_media_type="video",
        source_file_name="demo.mp4",
        user_id="U12345",
    )


@pytest.mark.asyncio
async def test_handle_media_message_infers_audio(mock_event):
    message = MagicMock(spec=FileMessageContent)
    message.id = "M125"
    message.type = "file"
    message.file_name = "voice.mp3"
    mock_event.message = message

    context = LineEventContext(mock_event)

    with patch(
        "app.services.line.event_handler.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process, patch(
        "app.services.line.event_handler.get_line_message_service",
        return_value=_mock_line_service(),
    ):
        await context.handle_media_message()

    mock_process.assert_called_once_with(
        media_message_id="M125",
        user_media_type="audio",
        source_file_name="voice.mp3",
        user_id="U12345",
    )


@pytest.mark.asyncio
async def test_handle_media_message_unknown_file(mock_event):
    message = MagicMock(spec=FileMessageContent)
    message.id = "M126"
    message.type = "file"
    message.file_name = "document.pdf"
    mock_event.message = message

    context = LineEventContext(mock_event)

    with patch(
        "app.services.line.event_handler.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process, patch(
        "app.services.line.event_handler.get_line_message_service",
        return_value=_mock_line_service(),
    ):
        await context.handle_media_message()

    mock_process.assert_called_once_with(
        media_message_id="M126",
        user_media_type="file",
        source_file_name="document.pdf",
        user_id="U12345",
    )


@pytest.mark.asyncio
async def test_handle_media_message_native_image(mock_event):
    message = MagicMock(spec=ImageMessageContent)
    message.id = "M127"
    message.type = "image"
    message.file_name = None
    mock_event.message = message

    context = LineEventContext(mock_event)

    with patch(
        "app.services.line.event_handler.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process, patch(
        "app.services.line.event_handler.get_line_message_service",
        return_value=_mock_line_service(),
    ):
        await context.handle_media_message()

    mock_process.assert_called_once_with(
        media_message_id="M127",
        user_media_type="image",
        source_file_name=None,
        user_id="U12345",
    )
