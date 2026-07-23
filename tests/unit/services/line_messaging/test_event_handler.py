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
    TextMessageContent,
    UserSource,
)

from app.models.chat_message import ChatMessage
from app.services.history.history_service import LineMessageHistoryService
from app.services.line_messaging.dispatcher.dispatcher import (
    LineEventDispatcher as LineEventHandler,
)
from app.services.line_messaging.handler.message_handler import LineValidationError


def create_handler(
    agent,
    token_manager,
    history_service,
    user_profile_service,
    tts_service,
):
    from app.services.line_messaging.handler.location_handler import LineLocationHandler
    from app.services.line_messaging.handler.media_handler import LineMediaHandler
    from app.services.line_messaging.handler.message_handler import LineMessageHandler
    from app.services.line_messaging.reply.reply import LineReplier

    replier = LineReplier(token_manager=token_manager, tts_service=tts_service)
    message_handler = LineMessageHandler(
        agent=agent,
        history_service=history_service,
        user_profile_service=user_profile_service,
        replier=replier,
    )
    media_handler = LineMediaHandler(
        agent=agent,
        history_service=history_service,
        user_profile_service=user_profile_service,
        replier=replier,
    )
    location_handler = LineLocationHandler(
        agent=agent,
        history_service=history_service,
        user_profile_service=user_profile_service,
        replier=replier,
    )
    return LineEventHandler(
        message_handler=message_handler,
        media_handler=media_handler,
        location_handler=location_handler,
        replier=replier,
    )


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


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.invoke = AsyncMock(return_value={"response": "AI 回覆"})
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
    return svc


@pytest.fixture
def mock_tts_service():
    svc = MagicMock()
    svc.synthesize.return_value = (b"", "https://cdn.example/tts/test.mp3", 1234)
    return svc


@pytest.fixture
def handler(
    mock_agent,
    mock_history_service,
    mock_user_profile_service,
    mock_tts_service,
):
    token_manager = MagicMock()
    token_manager.get_token.return_value = "dummy_token"
    return create_handler(
        agent=mock_agent,
        token_manager=token_manager,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        tts_service=mock_tts_service,
    )


@pytest.fixture(autouse=True)
def mock_line_api():
    with patch("app.services.line_messaging.reply.reply.Configuration"), patch(
        "app.services.line_messaging.reply.reply.ApiClient"
    ), patch(
        "app.services.line_messaging.reply.reply.MessagingApi"
    ) as mock_messaging_api:
        messaging_api = MagicMock()
        mock_messaging_api.return_value = messaging_api
        yield messaging_api


@pytest.mark.asyncio
async def test_handle_text_message_success_adds_tts_audio(
    handler,
    mock_agent,
    mock_line_api,
    mock_history_service,
):
    message = TextMessageContent(id="M1", text="你好", quoteToken="dummy")
    event = _message_event(message)
    mock_history_service.load_history.return_value = [HumanMessage(content="你好")]

    await handler.handle(event)

    mock_history_service.load_history.assert_called_once_with(
        user_id="U12345",
        current_input="你好",
        message_type="text",
    )
    mock_agent.invoke.assert_called_once_with(
        user_input="你好",
        messages=[HumanMessage(content="你好")],
        user_profile={"voice_reply_enabled": True},
    )

    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.reply_token == "dummy_token"
    assert reply_req.messages[0].text == "AI 回覆"
    assert reply_req.messages[1].type == "audio"
    assert reply_req.messages[1].original_content_url == (
        "https://cdn.example/tts/test.mp3"
    )
    assert reply_req.messages[1].duration == 1234
    mock_history_service.save_turn.assert_called_once()


@pytest.mark.asyncio
async def test_handle_text_message_voice_disabled_sends_text_only(
    handler,
    mock_line_api,
    mock_tts_service,
    mock_user_profile_service,
):
    mock_user_profile_service.get_user_profile.return_value = {
        "voice_reply_enabled": False
    }
    message = TextMessageContent(id="M1", text="你好", quoteToken="dummy")

    await handler.handle(_message_event(message))

    mock_tts_service.synthesize.assert_not_called()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert len(reply_req.messages) == 1


@pytest.mark.asyncio
async def test_handle_text_message_request_location(handler, mock_agent, mock_line_api):
    message = TextMessageContent(id="M_HOSP", text="附近有醫院嗎", quoteToken="dummy")
    mock_agent.invoke.return_value = {
        "response": "請分享位置",
        "call_request_location": True,
    }

    await handler.handle(_message_event(message))

    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.messages[0].text == "請分享位置"
    assert reply_req.messages[0].quick_reply is not None


@pytest.mark.asyncio
async def test_handle_text_message_error_fallback(
    handler,
    mock_agent,
    mock_line_api,
    mock_history_service,
    mock_tts_service,
):
    message = TextMessageContent(id="M3", text="hi", quoteToken="dummy")
    mock_agent.invoke.side_effect = Exception("AI Crash")

    await handler.handle(_message_event(message))

    mock_tts_service.synthesize.assert_not_called()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert "發生錯誤" in reply_req.messages[0].text
    mock_history_service.save_turn.assert_not_called()


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

    agent_input = mock_agent.invoke.call_args[1]["user_input"]
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
async def test_handle_media_message_empty_ocr_returns_error(
    handler,
    mock_agent,
    mock_line_api,
):
    message = ImageMessageContent(
        id="M127_empty",
        contentProvider={"type": "line"},
        quoteToken="qt",
    )

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="Unable to extract text from media file (no content extracted)",
    ):
        await handler.handle(_message_event(message))

    mock_agent.invoke.assert_not_called()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert "無法從您傳送的" in reply_req.messages[0].text


@pytest.mark.asyncio
async def test_local_tts_file_uses_public_base_url(
    handler,
    mock_tts_service,
    mock_line_api,
    monkeypatch,
):
    audio_file = Path("app_data") / "tmp" / "tts_test.mp3"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"mp3")
    mock_tts_service.synthesize.return_value = (b"mp3", str(audio_file), 2345)
    monkeypatch.setattr(
        "app.services.line_messaging.reply.reply.settings.PUBLIC_BASE_URL",
        "https://example.com",
    )
    monkeypatch.setattr(
        "app.services.line_messaging.reply.reply.settings.TTS_AUDIO_URL_PATH",
        "/tts",
    )

    message = TextMessageContent(id="M1", text="你好", quoteToken="dummy")
    await handler.handle(_message_event(message))

    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.messages[1].original_content_url == (
        "https://example.com/tts/tts_test.mp3"
    )
    assert reply_req.messages[1].duration == 2345
    audio_file.unlink(missing_ok=True)


def test_validate_media_message_success() -> None:
    assert issubclass(LineValidationError, Exception)


@pytest.mark.asyncio
async def test_handle_media_message_invalid_type(handler, mock_line_api):
    message = FileMessageContent(
        id="M123",
        fileName="test.invalid",
        fileSize=100,
    )
    message.type = "unsupported_media_type"

    await handler.handle(_message_event(message))

    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert "不支援的媒體類型" in reply_req.messages[0].text


@pytest.mark.asyncio
async def test_handle_media_message_invalid_filename(handler, mock_line_api):
    message = FileMessageContent(id="M123", fileName="   ", fileSize=100)
    message.type = "file"

    await handler.handle(_message_event(message))

    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert "不支援的媒體類型" in reply_req.messages[0].text or "無效的媒體檔名" in reply_req.messages[0].text


@pytest.mark.asyncio
async def test_history_service_load_converts_correctly():
    mock_repo = MagicMock()
    mock_repo.list_messages = AsyncMock(
        return_value=[
            ChatMessage(
                line_id="user_1",
                message_type="text",
                content="哈囉",
                timestamp=datetime.now(),
            ),
            ChatMessage(
                line_id="user_1",
                message_type="assistant_reply",
                content="你好",
                timestamp=datetime.now(),
            ),
        ]
    )

    svc = LineMessageHistoryService(mock_repo)
    chat_history = await svc.load_history("user_1", "當前問題", "text")

    assert len(chat_history) == 2
    assert isinstance(chat_history[0], HumanMessage)
    assert chat_history[0].content == "哈囉"
    assert isinstance(chat_history[1], AIMessage)
    assert chat_history[1].content == "你好"


@pytest.mark.asyncio
async def test_history_service_save_turn_appends_messages():
    mock_repo = MagicMock()
    mock_repo.append_message = AsyncMock()

    svc = LineMessageHistoryService(mock_repo)
    dt = datetime.now()
    await svc.save_turn("user_1", "哈囉", "回答", "text", dt)

    assert mock_repo.append_message.call_count == 2
    user_call, ai_call = mock_repo.append_message.call_args_list

    user_msg = user_call[0][1]
    assert user_msg.line_id == "user_1"
    assert user_msg.message_type == "text"
    assert user_msg.content == "哈囉"
    assert user_msg.timestamp == dt

    ai_msg = ai_call[0][1]
    assert ai_msg.line_id == "user_1"
    assert ai_msg.message_type == "assistant_reply"
    assert ai_msg.content == "回答"


@pytest.mark.asyncio
async def test_history_service_load_slices_to_last_five():
    mock_repo = MagicMock()
    mock_repo.list_messages = AsyncMock(
        return_value=[
            ChatMessage(
                line_id="user_1",
                message_type="text",
                content=f"msg_{i}",
                timestamp=datetime.now(),
            )
            for i in range(7)
        ]
    )

    svc = LineMessageHistoryService(mock_repo)
    chat_history = await svc.load_history("user_1", "當前問題", "text")

    assert len(chat_history) == 5
    assert chat_history[0].content == "msg_2"
    assert chat_history[4].content == "msg_6"


@pytest.mark.asyncio
async def test_handle_text_message_with_user_profile(
    mock_agent, mock_line_api, mock_history_service, mock_tts_service
):
    mock_profile_service = MagicMock()
    dummy_profile = {"name": "張三", "gender": "male", "voice_reply_enabled": True}
    mock_profile_service.get_user_profile = AsyncMock(return_value=dummy_profile)

    token_manager = MagicMock()
    token_manager.get_token.return_value = "dummy_token"

    handler = create_handler(
        agent=mock_agent,
        token_manager=token_manager,
        history_service=mock_history_service,
        user_profile_service=mock_profile_service,
        tts_service=mock_tts_service,
    )

    message = TextMessageContent(id="M_PROF", text="你好", quoteToken="dummy")
    mock_history_service.load_history.return_value = [HumanMessage(content="你好")]

    await handler.handle(_message_event(message, user_id="U_PROF"))

    mock_profile_service.get_user_profile.assert_called_once_with("U_PROF")
    mock_agent.invoke.assert_called_once_with(
        user_input="你好",
        messages=[HumanMessage(content="你好")],
        user_profile=dummy_profile,
    )
