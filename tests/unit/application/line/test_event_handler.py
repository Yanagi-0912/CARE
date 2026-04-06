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

from app.application.line.event_handler import LineEventHandler


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
    svc.send_location_quick_reply = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def mock_response_orchestrator():
    from app.infrastructure.gemini import GeminiResult
    orc = MagicMock()
    orc.orchestrate_response = AsyncMock(return_value=GeminiResult(text="AI 回覆"))
    return orc


@pytest.fixture
def mock_medical_service():
    return MagicMock()


@pytest.fixture
def handler(mock_response_orchestrator, mock_line_message_service, mock_medical_service):
    return LineEventHandler(
        mock_response_orchestrator, mock_line_message_service, mock_medical_service
    )


@pytest.mark.asyncio
async def test_handle_media_message_infers_image(handler, mock_response_orchestrator, mock_line_message_service):
    message = FileMessageContent(
        id="M123", fileName="test.png", fileSize=100, quoteToken="dummy"
    )
    event = _message_event(message)

    with patch(
        "app.application.media.mutimedia_processor.media_processor_service.process_media",
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
    mock_response_orchestrator.orchestrate_response.assert_called_once()
    orchestrator_input = mock_response_orchestrator.orchestrate_response.call_args[0][0]
    assert "image" in orchestrator_input
    assert "[processed]" in orchestrator_input
    
    mock_line_message_service.send_line_reply.assert_called_once_with(
        "dummy_token", "AI 回覆", "U12345"
    )


@pytest.mark.asyncio
async def test_handle_media_message_infers_video(handler, mock_response_orchestrator, mock_line_message_service):
    message = FileMessageContent(
        id="M124", fileName="demo.mp4", fileSize=200, quoteToken="dummy"
    )
    event = _message_event(message)

    with patch(
        "app.application.media.mutimedia_processor.media_processor_service.process_media",
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
async def test_handle_media_message_infers_audio(handler, mock_response_orchestrator, mock_line_message_service):
    message = FileMessageContent(
        id="M125", fileName="voice.mp3", fileSize=300, quoteToken="dummy"
    )
    event = _message_event(message)

    with patch(
        "app.application.media.mutimedia_processor.media_processor_service.process_media",
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
async def test_handle_media_message_unknown_file(handler, mock_response_orchestrator, mock_line_message_service):
    message = FileMessageContent(
        id="M126", fileName="document.pdf", fileSize=400, quoteToken="dummy"
    )
    event = _message_event(message)

    with patch(
        "app.application.media.mutimedia_processor.media_processor_service.process_media",
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
async def test_handle_media_message_native_image(handler, mock_response_orchestrator, mock_line_message_service):
    message = ImageMessageContent(
        id="M127",
        contentProvider={"type": "line"},
        quoteToken="qt",
    )
    event = _message_event(message)

    with patch(
        "app.application.media.mutimedia_processor.media_processor_service.process_media",
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
async def test_handle_text_message_success(handler, mock_response_orchestrator, mock_line_message_service):
    from linebot.v3.webhooks import TextMessageContent
    message = TextMessageContent(id="M1", text="你好", quoteToken="dummy")
    event = _message_event(message)
    
    await handler.handle(event)
    
    mock_response_orchestrator.orchestrate_response.assert_called_once_with("你好")
    mock_line_message_service.send_line_reply.assert_called_once_with("dummy_token", "AI 回覆", "U12345")


@pytest.mark.asyncio
async def test_handle_text_message_function_call(handler, mock_response_orchestrator, mock_line_message_service):
    from linebot.v3.webhooks import TextMessageContent
    from app.infrastructure.gemini import GeminiResult
    message = TextMessageContent(id="M2", text="附近有醫院嗎", quoteToken="dummy")
    event = _message_event(message)
    
    mock_response_orchestrator.orchestrate_response.return_value = GeminiResult(
        function_name="request_location"
    )
    
    await handler.handle(event)
    
    mock_line_message_service.send_location_quick_reply.assert_called_once_with("dummy_token", "U12345")


@pytest.mark.asyncio
async def test_handle_text_message_error_fallback(handler, mock_response_orchestrator, mock_line_message_service):
    from linebot.v3.webhooks import TextMessageContent
    message = TextMessageContent(id="M3", text="hi", quoteToken="dummy")
    event = _message_event(message)
    
    mock_response_orchestrator.orchestrate_response.side_effect = Exception("AI Crash")
    
    await handler.handle(event)
    
    mock_line_message_service.send_line_reply.assert_called_once()
    assert "發生錯誤" in mock_line_message_service.send_line_reply.call_args[0][1]
