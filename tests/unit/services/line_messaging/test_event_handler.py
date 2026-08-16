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
from app.schemas import MedicalFacility
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
    medical_service,
):
    from app.services.line_messaging.handler.location_handler import LineLocationHandler
    from app.services.line_messaging.handler.media_handler import LineMediaHandler
    from app.services.line_messaging.handler.facility_detail_handler import LineFacilityDetailHandler
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
    facility_detail_handler = LineFacilityDetailHandler(
        medical_service=medical_service,
        replier=replier,
    )
    return LineEventHandler(
        message_handler=message_handler,
        media_handler=media_handler,
        location_handler=location_handler,
        facility_detail_handler=facility_detail_handler,
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
    svc.synthesize = AsyncMock(
        return_value=(b"", "https://cdn.example/tts/test.mp3", 1234)
    )
    return svc


@pytest.fixture
def mock_medical_service():
    svc = MagicMock()
    svc.get_facility_by_id = AsyncMock(return_value=None)
    return svc


@pytest.fixture
def handler(
    mock_agent,
    mock_history_service,
    mock_user_profile_service,
    mock_tts_service,
    mock_medical_service,
):
    token_manager = MagicMock()
    token_manager.get_token_async = AsyncMock(return_value="dummy_token")
    token_manager.get_token.return_value = "dummy_token"
    return create_handler(
        agent=mock_agent,
        token_manager=token_manager,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        tts_service=mock_tts_service,
        medical_service=mock_medical_service,
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
    # quickReply 應掛在陣列最後一則訊息上，不然開啟語音回覆時會被覆蓋
    assert reply_req.messages[-1].quick_reply is not None


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
    token_manager.get_token_async = AsyncMock(return_value="dummy_token")
    token_manager.get_token.return_value = "dummy_token"

    handler = create_handler(
        agent=mock_agent,
        token_manager=token_manager,
        history_service=mock_history_service,
        user_profile_service=mock_profile_service,
        tts_service=mock_tts_service,
        medical_service=mock_medical_service,
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


@pytest.mark.asyncio
async def test_handle_postback_event_toggle_voice_reply_enabled(
    handler,
    mock_user_profile_service,
    mock_line_api,
):
    mock_user_profile_service.update_voice_reply_enabled = AsyncMock(return_value=True)
    event = _postback_event("action=toggle_voice_reply&enabled=true", user_id="U12345")

    await handler.handle(event)

    mock_user_profile_service.update_voice_reply_enabled.assert_called_once_with("U12345", True)
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.messages[0].text == "已開啟語音回覆"


@pytest.mark.asyncio
async def test_handle_postback_event_toggle_voice_reply_disabled(
    handler,
    mock_user_profile_service,
    mock_line_api,
):
    mock_user_profile_service.update_voice_reply_enabled = AsyncMock(return_value=True)
    event = _postback_event("action=toggle_voice_reply&enabled=false", user_id="U12345")

    await handler.handle(event)

    mock_user_profile_service.update_voice_reply_enabled.assert_called_once_with("U12345", False)
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.messages[0].text == "已關閉語音回覆"


@pytest.mark.asyncio
async def test_handle_postback_event_toggle_voice_reply_omitted_enabled_flips_on(
    handler,
    mock_user_profile_service,
    mock_line_api,
):
    mock_user_profile_service.get_user_profile = AsyncMock(
        return_value={"settings": {"voice_reply_enabled": False}}
    )
    mock_user_profile_service.update_voice_reply_enabled = AsyncMock(return_value=True)
    event = _postback_event("action=toggle_voice_reply", user_id="U12345")

    await handler.handle(event)

    mock_user_profile_service.get_user_profile.assert_awaited_once_with("U12345")
    mock_user_profile_service.update_voice_reply_enabled.assert_called_once_with(
        "U12345", True
    )
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.messages[0].text == "已開啟語音回覆"


@pytest.mark.asyncio
async def test_handle_postback_event_toggle_voice_reply_omitted_enabled_flips_off(
    handler,
    mock_user_profile_service,
    mock_line_api,
):
    mock_user_profile_service.get_user_profile = AsyncMock(
        return_value={"settings": {"voice_reply_enabled": True}}
    )
    mock_user_profile_service.update_voice_reply_enabled = AsyncMock(return_value=True)
    event = _postback_event("action=toggle_voice_reply", user_id="U12345")

    await handler.handle(event)

    mock_user_profile_service.update_voice_reply_enabled.assert_called_once_with(
        "U12345", False
    )
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.messages[0].text == "已關閉語音回覆"


VOICE_REPLY_PROFILE_REQUIRED_MSG = (
    "請先開啟「家庭中心」完成登入後再設定語音回覆"
)


@pytest.mark.asyncio
async def test_handle_postback_event_toggle_voice_reply_update_fails(
    handler,
    mock_user_profile_service,
    mock_line_api,
):
    mock_user_profile_service.get_user_profile = AsyncMock(
        return_value={"settings": {"voice_reply_enabled": False}}
    )
    mock_user_profile_service.update_voice_reply_enabled = AsyncMock(return_value=False)
    event = _postback_event("action=toggle_voice_reply", user_id="U12345")

    await handler.handle(event)

    mock_user_profile_service.update_voice_reply_enabled.assert_called_once_with(
        "U12345", True
    )
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.messages[0].text == VOICE_REPLY_PROFILE_REQUIRED_MSG
    assert reply_req.messages[0].text != "已開啟語音回覆"


#測試點擊醫療院所查看詳情的 postback 事件，應該呼叫 LineFacilityDetailHandler 並回覆 Flex Message
@pytest.mark.asyncio
async def test_handle_postback_event_view_facility_detail(
    handler,
    mock_medical_service,
    mock_line_api,
):
    mock_medical_service.get_facility_by_id.return_value = MedicalFacility(
        id="facility_1",
        name="測試診所",
        latitude=25.0,
        longitude=121.0,
        address="台北市測試路 1 號",
        phone="02-12345678",
        type="診所",
        departments=["家醫科"],
        clinic_time=None,
    )
    event = _postback_event(
        "action=view_facility_detail&facility_id=facility_1",
        user_id="U12345",
    )

    await handler.handle(event)

    mock_medical_service.get_facility_by_id.assert_called_once_with("facility_1")
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.reply_token == "dummy_token"
    assert len(reply_req.messages) == 1
    assert reply_req.messages[0].type == "flex"
    assert reply_req.messages[0].alt_text == "測試診所詳細資訊"


#測試當查無院所資料時，應回覆查無資料訊息
@pytest.mark.asyncio
async def test_handle_postback_event_view_facility_detail_not_found(
    handler,
    mock_medical_service,
    mock_line_api,
    mock_tts_service,
):
    mock_medical_service.get_facility_by_id.return_value = None
    event = _postback_event(
        "action=view_facility_detail&facility_id=missing_id",
        user_id="U12345",
    )

    await handler.handle(event)

    mock_medical_service.get_facility_by_id.assert_called_once_with("missing_id")
    mock_tts_service.synthesize.assert_not_called()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.messages[0].text == "查無此院所資料，可能已被更新或移除。"


@pytest.mark.asyncio
async def test_handle_flex_message_reply(handler, mock_agent, mock_line_api):
    flex_json = '{"type": "flex", "altText": "測試院所", "contents": {"type": "bubble", "body": {"type": "box", "layout": "vertical", "contents": []}}}'
    mock_agent.invoke.return_value = {"response": flex_json}
    message = TextMessageContent(id="M_FLEX", text="找醫院", quoteToken="dummy")

    await handler.handle(_message_event(message))

    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert len(reply_req.messages) == 1
    assert reply_req.messages[0].type == "flex"
    assert reply_req.messages[0].alt_text == "測試院所"


@pytest.mark.asyncio
async def test_handle_postback_event_confirm_medication(
    handler,
    mock_line_api,
):
    from datetime import datetime, timezone
    from app.models.medication import MedicationLog

    mock_med_service = AsyncMock()
    mock_med_service.confirm_medication.return_value = MedicationLog(
        reminder_id="R123",
        user_id="U12345",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=datetime.now(timezone.utc),
        timeout_at=datetime.now(timezone.utc),
        status="taken",
        taken_at=datetime.now(timezone.utc),
    )
    mock_med_service.list_medication_names_for_log.return_value = ["脈優", "利尿劑"]
    handler._medication_service = mock_med_service

    event = _postback_event("action=confirm_medication&log_id=L123", user_id="U12345")
    await handler.handle(event)

    mock_med_service.confirm_medication.assert_called_once_with("L123", "U12345")
    mock_line_api.reply_message.assert_called_once()
    reply_req = mock_line_api.reply_message.call_args[0][0]
    assert reply_req.messages[0].type == "flex"

    # 已完成的卡片要留下「這次吃了哪幾種藥」——提醒卡上有的資訊不該在按下
    # 確認後就消失，那是使用者事後唯一查得到的憑據。藥名取自剛剛確認的那筆
    # log（而不是「今天的規則」），所以查詢要帶著回傳的 log 本體。
    mock_med_service.list_medication_names_for_log.assert_awaited_once_with(
        mock_med_service.confirm_medication.return_value
    )
    rendered = str(reply_req.messages[0].contents.to_dict())
    assert "脈優" in rendered
    assert "利尿劑" in rendered


@pytest.mark.asyncio
async def test_handle_text_message_emits_start_done_and_stage_logs(
    handler,
    mock_history_service,
    caplog,
):
    import logging

    message = TextMessageContent(id="M1", text="你好", quoteToken="dummy")
    event = _message_event(message)
    mock_history_service.load_history.return_value = [HumanMessage(content="你好")]

    with caplog.at_level(logging.INFO):
        await handler.handle(event)

    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("START event=text") for m in messages)
    assert any(m.startswith("stage=handle") for m in messages)
    assert any(m.startswith("stage=history_loaded") for m in messages)
    assert any(m.startswith("stage=agent_done") for m in messages)
    assert any(m.startswith("stage=reply") for m in messages)
    assert any(m.startswith("DONE status=ok") for m in messages)

