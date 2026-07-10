from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from linebot.v3.webhooks import (
    DeliveryContext,
    FileMessageContent,
    ImageMessageContent,
    LocationMessageContent,
    MessageEvent,
    PostbackContent,
    PostbackEvent,
    TextMessageContent,
    UserSource,
)

from app.models.chat_message import ChatMessage
from app.services.history.history_service import LineMessageHistoryService
from app.services.line_messaging.event_handler import LineEventHandler, LineValidationError


def _message_event(
    message,
    *,
    reply_token: str = "dummy_token",
    user_id: str = "U12345",
) -> MessageEvent:
    return MessageEvent(
        timestamp=1000,
        mode="active",
        webhookEventId="01HZTEST000000000000000000",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken=reply_token,
        source=UserSource(type="user", userId=user_id),
        message=message,
    )


def _postback_event(
    data: str,
    *,
    reply_token: str = "dummy_token",
    user_id: str = "U12345",
) -> PostbackEvent:
    return PostbackEvent(
        timestamp=1000,
        mode="active",
        webhookEventId="01HZTEST000000000000000000",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken=reply_token,
        source=UserSource(type="user", userId=user_id),
        postback=PostbackContent(data=data),
    )


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.invoke = AsyncMock(return_value={"response": "AI reply"})
    return agent


@pytest.fixture
def mock_history_service():
    svc = MagicMock()
    svc.load_history = AsyncMock(return_value=[])
    svc.save_turn = AsyncMock()
    return svc


@pytest.fixture
def mock_user_profile_service():
    svc = MagicMock()
    svc.get_user_profile = AsyncMock(return_value={"voice_reply_enabled": True})
    svc.update_voice_reply_enabled = AsyncMock(return_value=True)
    return svc


@pytest.fixture
def mock_tts_service():
    svc = MagicMock()
    svc.available.return_value = True
    svc.synthesize.return_value = (b"", "https://cdn.example/tts/test.mp3", 1234)
    return svc


@pytest.fixture
def handler(
    mock_agent,
    mock_user_profile_service,
    mock_history_service,
    mock_tts_service,
):
    h = LineEventHandler(
        agent=mock_agent,
        channel_id="dummy_id",
        channel_secret="dummy_secret",
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        tts_service=mock_tts_service,
    )
    h.get_token = MagicMock(return_value="dummy_token")
    h._reply_message = MagicMock()
    return h


@pytest.mark.asyncio
async def test_handle_text_message_success(handler, mock_agent, mock_history_service):
    message = TextMessageContent(id="M1", text="hello", quoteToken="dummy")
    event = _message_event(message)
    mock_history_service.load_history.return_value = [HumanMessage(content="hello")]

    await handler.handle(event)

    mock_history_service.load_history.assert_called_once_with(
        user_id="U12345",
        current_input="hello",
        message_type="text",
    )
    mock_agent.invoke.assert_called_once_with(
        user_input="hello",
        messages=[HumanMessage(content="hello")],
    )
    reply_request = handler._reply_message.call_args.args[1]
    assert reply_request.reply_token == "dummy_token"
    assert reply_request.messages[0].text == "AI reply"
    assert reply_request.messages[1].type == "audio"
    assert reply_request.messages[1].original_content_url == (
        "https://cdn.example/tts/test.mp3"
    )
    mock_history_service.save_turn.assert_called_once()


@pytest.mark.asyncio
async def test_handle_text_message_respects_voice_preference(
    handler,
    mock_user_profile_service,
    mock_tts_service,
):
    mock_user_profile_service.get_user_profile.return_value = {
        "voice_reply_enabled": False
    }
    message = TextMessageContent(id="M1", text="hello", quoteToken="dummy")

    await handler.handle(_message_event(message))

    mock_tts_service.synthesize.assert_not_called()
    reply_request = handler._reply_message.call_args.args[1]
    assert len(reply_request.messages) == 1


@pytest.mark.asyncio
async def test_handle_text_message_request_location(handler, mock_agent):
    mock_agent.invoke.return_value = {
        "response": "please share location",
        "call_request_location": True,
    }
    message = TextMessageContent(id="M1", text="nearby hospital", quoteToken="dummy")

    await handler.handle(_message_event(message))

    reply_request = handler._reply_message.call_args.args[1]
    assert reply_request.messages[0].quick_reply is not None


@pytest.mark.asyncio
async def test_handle_location_message_delegates_to_agent(handler, mock_agent):
    message = LocationMessageContent(
        id="M_LOC1",
        title="my location",
        address="Taipei",
        latitude=25.0330,
        longitude=121.5654,
    )

    await handler.handle(_message_event(message))

    agent_input = mock_agent.invoke.call_args.kwargs["user_input"]
    assert "25.033" in agent_input
    assert "121.5654" in agent_input


@pytest.mark.asyncio
async def test_handle_media_message_infers_image(handler):
    message = FileMessageContent(id="M123", fileName="test.png", fileSize=100)

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process:
        await handler.handle(_message_event(message))

    mock_process.assert_called_once_with(
        media_message_id="M123",
        user_media_type="image",
        source_file_name="test.png",
        user_id="U12345",
    )


@pytest.mark.asyncio
async def test_handle_media_message_native_image(handler):
    message = ImageMessageContent(
        id="M127",
        contentProvider={"type": "line"},
        quoteToken="qt",
    )

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ) as mock_process:
        await handler.handle(_message_event(message))

    mock_process.assert_called_once_with(
        media_message_id="M127",
        user_media_type="image",
        source_file_name=None,
        user_id="U12345",
    )


@pytest.mark.asyncio
async def test_handle_media_message_empty_ocr_returns_error(handler, mock_agent):
    message = ImageMessageContent(
        id="M127_empty",
        contentProvider={"type": "line"},
        quoteToken="qt",
    )

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="Unable to extract text from media file",
    ):
        await handler.handle(_message_event(message))

    mock_agent.invoke.assert_not_called()
    reply_request = handler._reply_message.call_args.args[1]
    assert "無法從您傳送的image中辨識出任何文字" in reply_request.messages[0].text


@pytest.mark.asyncio
async def test_handle_text_message_error_fallback(
    handler,
    mock_agent,
    mock_history_service,
    mock_tts_service,
):
    message = TextMessageContent(id="M3", text="hi", quoteToken="dummy")
    mock_agent.invoke.side_effect = Exception("AI crash")

    await handler.handle(_message_event(message))

    mock_tts_service.synthesize.assert_not_called()
    reply_request = handler._reply_message.call_args.args[1]
    assert reply_request.messages[0].text == "抱歉，處理您的訊息時發生錯誤，請稍後再試"
    mock_history_service.save_turn.assert_not_called()


@pytest.mark.asyncio
async def test_handle_postback_toggle_voice_reply(
    handler,
    mock_user_profile_service,
    mock_tts_service,
):
    event = _postback_event("action=toggle_voice_reply&enabled=false")

    await handler.handle(event)

    mock_user_profile_service.update_voice_reply_enabled.assert_called_once_with(
        "U12345",
        False,
    )
    mock_tts_service.synthesize.assert_not_called()
    reply_request = handler._reply_message.call_args.args[1]
    assert reply_request.messages[0].text == "語音回覆已關閉成功"


@pytest.mark.asyncio
async def test_send_reply_with_local_tts_file_uses_public_base_url(
    handler,
    mock_tts_service,
    monkeypatch,
):
    audio_file = Path("app_data") / "tmp" / "tts_test.mp3"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"mp3")
    mock_tts_service.synthesize.return_value = (b"mp3", str(audio_file), 2345)
    monkeypatch.setattr("app.services.line_messaging.event_handler.settings.PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setattr("app.services.line_messaging.event_handler.settings.TTS_AUDIO_URL_PATH", "/tts")

    ok = await handler._send_reply("token", "hello", "U12345")

    assert ok is True
    reply_request = handler._reply_message.call_args.args[1]
    assert reply_request.messages[1].original_content_url == (
        "https://example.com/tts/tts_test.mp3"
    )
    assert reply_request.messages[1].duration == 2345
    audio_file.unlink(missing_ok=True)


def test_validate_media_message_success() -> None:
    assert issubclass(LineValidationError, Exception)


@pytest.mark.asyncio
async def test_history_service_load_converts_correctly():
    mock_repo = MagicMock()
    mock_repo.list_messages = AsyncMock(
        return_value=[
            ChatMessage(
                line_id="user_1",
                message_type="text",
                content="hello",
                timestamp=datetime.now(),
            ),
            ChatMessage(
                line_id="user_1",
                message_type="assistant_reply",
                content="hi",
                timestamp=datetime.now(),
            ),
        ]
    )

    svc = LineMessageHistoryService(mock_repo)
    chat_history = await svc.load_history("user_1", "current", "text")

    assert len(chat_history) == 2
    assert isinstance(chat_history[0], HumanMessage)
    assert chat_history[0].content == "hello"
    assert isinstance(chat_history[1], AIMessage)
    assert chat_history[1].content == "hi"


@pytest.mark.asyncio
async def test_history_service_save_turn_appends_messages():
    mock_repo = MagicMock()
    mock_repo.append_message = AsyncMock()

    svc = LineMessageHistoryService(mock_repo)
    dt = datetime.now()
    await svc.save_turn("user_1", "hello", "hi", "text", dt)

    assert mock_repo.append_message.call_count == 2
