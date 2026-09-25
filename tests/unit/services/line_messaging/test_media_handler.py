from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from linebot.v3.webhooks import FileMessageContent

from app.services.medical.symptom_classification.urgency import AffectedPerson
from app.services.safety.emergency_alert_service import EmergencyFamilyAlertService
from app.services.medical.symptom_classification.urgency import (
    URGENCY_EMERGENCY,
    UrgencyVerdict,
)
from app.services.line_messaging.handler.media_handler import LineMediaHandler


@pytest.fixture
def media_handler(mock_agent, mock_history_service, mock_user_profile_service):
    replier = MagicMock()
    return LineMediaHandler(
        agent=mock_agent,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        replier=replier,
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


@pytest.mark.asyncio
async def test_extract_media_text_file_success_calls_ingest(media_handler):
    message = FileMessageContent(id="M_PDF", fileName="report.pdf", fileSize=100)
    message.type = "file"
    raw_content = "PDF extracted text content"

    mock_ingest = MagicMock()
    mock_ingest.ingest_text = AsyncMock(return_value="doc-id")
    media_handler._user_document_ingest_service = mock_ingest

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value=raw_content,
    ):
        user_text, media_type, _ = await media_handler._extract_media_text(
            message, "U12345"
        )

    mock_ingest.ingest_text.assert_called_once_with(
        "U12345",
        raw_content,
        source_name="report.pdf",
        media_type="file",
    )
    assert user_text == f"以下為使用者傳送的file媒體內容：\n{raw_content}"
    assert media_type == "file"


@pytest.mark.asyncio
async def test_extract_media_text_ingest_failure_still_returns_prefixed_text(
    media_handler,
):
    message = FileMessageContent(id="M_PDF", fileName="report.pdf", fileSize=100)
    message.type = "file"
    raw_content = "PDF extracted text content"

    mock_ingest = MagicMock()
    mock_ingest.ingest_text = AsyncMock(side_effect=RuntimeError("ingest failed"))
    media_handler._user_document_ingest_service = mock_ingest

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value=raw_content,
    ):
        user_text, media_type, _ = await media_handler._extract_media_text(
            message, "U12345"
        )

    mock_ingest.ingest_text.assert_called_once()
    assert user_text == f"以下為使用者傳送的file媒體內容：\n{raw_content}"
    assert media_type == "file"


@pytest.mark.asyncio
async def test_extract_media_text_image_does_not_call_ingest(media_handler):
    message = FileMessageContent(id="M123", fileName="test.png", fileSize=100)

    mock_ingest = MagicMock()
    mock_ingest.ingest_text = AsyncMock()
    media_handler._user_document_ingest_service = mock_ingest

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="[processed]",
    ):
        await media_handler._extract_media_text(message, "U12345")

    mock_ingest.ingest_text.assert_not_called()


class FakeSafetyAlertService:
    def __init__(self):
        self.calls = []

    async def check(self, user_id, text):
        self.calls.append((user_id, text))


async def _drain(handler):
    import asyncio

    tasks = list(handler._safety_alert_tasks)
    if tasks:
        await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_ocr_text_reaches_the_safety_check(
    mock_agent, mock_history_service, mock_user_profile_service
):
    """圖片的訊號全部都在既有管線 OCR 出來的文字裡，不需要再處理一次影像。"""
    from datetime import datetime

    from linebot.v3.webhooks import (
        ContentProvider,
        DeliveryContext,
        ImageMessageContent,
        MessageEvent,
        UserSource,
    )

    safety = FakeSafetyAlertService()
    replier = MagicMock()
    replier.reply = AsyncMock(return_value=True)
    handler = LineMediaHandler(
        agent=mock_agent,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        replier=replier,
        safety_alert_service=safety,
    )
    event = MessageEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZTEST000000000000000001",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt",
        source=UserSource(type="user", userId="U12345"),
        message=ImageMessageContent(
            id="M_IMG",
            quoteToken="qt",
            contentProvider=ContentProvider(type="line"),
        ),
    )

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="合利他命強効錠 EX PLUS　アリナミン",
    ):
        await handler.handle(event)
    await _drain(handler)

    assert len(safety.calls) == 1
    assert "合利他命強効錠 EX PLUS" in safety.calls[0][1]


class FakeEmergencyFamilyAlertService(EmergencyFamilyAlertService):
    """選病人用正式邏輯（patients_to_notify），只把推播換成記錄。"""

    def __init__(self):
        super().__init__(replier=None)
        self.calls = []

    async def notify(self, user_id, reason, patient_words="", *, reporter_id=""):
        self.calls.append((user_id, reason, patient_words))
        return "no_recipient"


class _SelfIdentifier:
    """紅卡之後的人物辨識替身：把事件歸給發話者本人。"""

    async def identify_affected(self, verdict, text, *, language, earlier=()):
        from dataclasses import replace

        return replace(verdict, affected=(AffectedPerson(kind="self", event="胸痛"),))


@pytest.mark.asyncio
async def test_an_emergency_described_in_a_voice_message_notifies_the_family(
    mock_history_service, mock_user_profile_service
):
    """
    語音、圖片、檔案抽出的文字判定為緊急時，家人通報要和文字訊息一樣發出。
    先前正式組裝的 LineMediaHandler 沒有收到通報服務：當事人拿到紅卡，家人什麼
    都沒收到——而長輩最常用的就是語音。
    """
    from datetime import datetime

    from linebot.v3.webhooks import (
        AudioMessageContent,
        ContentProvider,
        DeliveryContext,
        MessageEvent,
        UserSource,
    )

    agent = MagicMock()
    agent.invoke = AsyncMock(
        return_value={
            "response": "緊急卡",
            "emergency": True,
            "urgency_verdict": UrgencyVerdict(level=URGENCY_EMERGENCY, display="你提到有人叫不醒"),
        }
    )
    emergency = FakeEmergencyFamilyAlertService()
    replier = MagicMock()
    replier.reply = AsyncMock(return_value=True)
    handler = LineMediaHandler(
        agent=agent,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        replier=replier,
        emergency_family_alert_service=emergency,
        urgency_classifier=_SelfIdentifier(),
    )
    event = MessageEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZTEST000000000000000002",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt",
        source=UserSource(type="user", userId="U12345"),
        message=AudioMessageContent(
            id="M_AUDIO",
            duration=3000,
            contentProvider=ContentProvider(type="line"),
        ),
    )

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="我胸口好痛喘不過氣",
    ):
        await handler.handle(event)
    await _drain(handler)

    assert len(emergency.calls) == 1
    user_id, reason, words = emergency.calls[0]
    assert user_id == "U12345"
    assert reason == "你提到有人叫不醒"
    assert "我胸口好痛喘不過氣" in words


@pytest.mark.asyncio
async def test_media_processor_call_is_unchanged_by_the_safety_check(
    mock_agent, mock_history_service, mock_user_profile_service
):
    """既有媒體路徑的行為 SHALL 與本能力導入前完全相同。"""
    message = FileMessageContent(id="M_PDF", fileName="report.pdf", fileSize=100)
    message.type = "file"
    handler = LineMediaHandler(
        agent=mock_agent,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        replier=MagicMock(),
        safety_alert_service=FakeSafetyAlertService(),
    )

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="PDF extracted text content",
    ) as mock_process:
        user_text, media_type, _ = await handler._extract_media_text(message, "U12345")

    mock_process.assert_awaited_once_with(
        media_message_id="M_PDF",
        user_media_type="file",
        source_file_name="report.pdf",
        user_id="U12345",
    )
    assert user_text == "以下為使用者傳送的file媒體內容：\nPDF extracted text content"
    assert media_type == "file"


@pytest.mark.asyncio
async def test_failed_media_pipeline_never_reaches_the_safety_check(
    mock_agent, mock_history_service, mock_user_profile_service
):
    """管線回錯誤字串時主回覆是「請重新傳送」，那串字沒有評估的價值。"""
    from datetime import datetime

    from linebot.v3.webhooks import (
        ContentProvider,
        DeliveryContext,
        ImageMessageContent,
        MessageEvent,
        UserSource,
    )

    safety = FakeSafetyAlertService()
    replier = MagicMock()
    replier.reply = AsyncMock(return_value=True)
    handler = LineMediaHandler(
        agent=mock_agent,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        replier=replier,
        safety_alert_service=safety,
    )
    event = MessageEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZTEST000000000000000002",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt",
        source=UserSource(type="user", userId="U12345"),
        message=ImageMessageContent(
            id="M_IMG",
            quoteToken="qt",
            contentProvider=ContentProvider(type="line"),
        ),
    )

    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="Unable to extract text from image",
    ):
        with pytest.raises(Exception):
            await handler.handle(event)

    assert safety.calls == []


# n8n 影像解析節點對手寫表格的輸出形式：標題一行、空行、Markdown 表格
TABLE_TEXT = """血壓紀錄

| 日期 | 收縮壓 | 舒張壓 |
| --- | --- | --- |
| 9/1 | 138 | 82 |
| 9/2 | 138（↓同上） | 80 |"""


def _event(message):
    from datetime import datetime

    from linebot.v3.webhooks import DeliveryContext, MessageEvent, UserSource

    return MessageEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZTEST000000000000000003",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt",
        source=UserSource(type="user", userId="U12345"),
        message=message,
    )


def _handler_with_real_replier(agent, history_service, user_profile_service):
    from app.services.line_messaging.reply.reply import LineReplier
    from tests.conftest import fake_line_token_manager

    return LineMediaHandler(
        agent=agent,
        history_service=history_service,
        user_profile_service=user_profile_service,
        replier=LineReplier(
            token_manager=fake_line_token_manager("token"), tts_service=None
        ),
    )


async def _handle_and_capture(handler, event, recognized_text):
    """跑完整條 handle()，只擋掉影像解析與對外的 LINE SDK 呼叫，回傳實際送出的訊息。"""
    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value=recognized_text,
    ), patch("app.services.line_messaging.reply.reply.Configuration"), patch(
        "app.services.line_messaging.reply.reply.ApiClient"
    ), patch(
        "app.services.line_messaging.reply.reply.MessagingApi"
    ) as mock_messaging_api:
        messaging_api = MagicMock()
        mock_messaging_api.return_value = messaging_api
        await handler.handle(event)
    return messaging_api.reply_message.call_args[0][0].messages


@pytest.mark.asyncio
async def test_photo_of_a_table_gets_a_table_card_before_the_answer(
    mock_agent, mock_history_service, mock_user_profile_service
):
    """卡片要用辨識原文組：誤用加了「以下為使用者傳送的…」前綴的字串，標題會變成那行前綴。"""
    from linebot.v3.messaging import FlexMessage, TextMessage
    from linebot.v3.webhooks import ContentProvider, ImageMessageContent

    handler = _handler_with_real_replier(
        mock_agent, mock_history_service, mock_user_profile_service
    )
    event = _event(
        ImageMessageContent(
            id="M_IMG", quoteToken="qt", contentProvider=ContentProvider(type="line")
        )
    )

    sent = await _handle_and_capture(handler, event, TABLE_TEXT)

    assert len(sent) == 2
    assert isinstance(sent[0], FlexMessage)
    assert sent[0].contents.to_dict()["header"]["contents"][0]["text"] == "血壓紀錄"
    assert isinstance(sent[1], TextMessage)
    assert sent[1].text == "AI 回覆"


@pytest.mark.asyncio
async def test_pdf_with_a_table_gets_no_table_card(
    mock_agent, mock_history_service, mock_user_profile_service
):
    """表格卡只給圖片：PDF 在 n8n 走的是另一條解析路徑，不是產出這種表格格式的影像 prompt。"""
    from linebot.v3.messaging import TextMessage

    message = FileMessageContent(id="M_PDF", fileName="report.pdf", fileSize=100)
    message.type = "file"
    handler = _handler_with_real_replier(
        mock_agent, mock_history_service, mock_user_profile_service
    )

    sent = await _handle_and_capture(handler, _event(message), TABLE_TEXT)

    assert len(sent) == 1
    assert isinstance(sent[0], TextMessage)


@pytest.mark.asyncio
async def test_emergency_reply_is_not_preceded_by_a_table_card(
    mock_history_service, mock_user_profile_service
):
    """緊急時紅卡要是第一則（見 _process_and_reply 家人通報處的註解），表格卡不送。"""
    from linebot.v3.messaging import TextMessage
    from linebot.v3.webhooks import ContentProvider, ImageMessageContent

    agent = MagicMock()
    agent.invoke = AsyncMock(
        return_value={
            "response": "緊急卡",
            "emergency": True,
            "urgency_verdict": UrgencyVerdict(level=URGENCY_EMERGENCY, display="紀錄上寫胸口痛"),
        }
    )
    handler = _handler_with_real_replier(
        agent, mock_history_service, mock_user_profile_service
    )
    event = _event(
        ImageMessageContent(
            id="M_IMG", quoteToken="qt", contentProvider=ContentProvider(type="line")
        )
    )

    sent = await _handle_and_capture(handler, event, TABLE_TEXT)

    assert len(sent) == 1
    assert isinstance(sent[0], TextMessage)
    assert sent[0].text == "緊急卡"


# 語音逐字稿就是使用者親口問的問題，要跟打字一樣進 agent：能查知識庫、能觸發找院所。
# 包上「以下為使用者傳送的audio媒體內容：」的話，nodes.py 的 _is_media_extracted_content
# 與 prompt 規則 (e) 會把它當成 OCR／文件抽字而禁止 get_rag_answer——2026-09-14 用語音問
# 「肚子痛的原因是什麼啊」就因此沒有查知識庫。那個前綴是給圖片、文件抽出的全文用的。
@pytest.mark.asyncio
async def test_voice_message_transcript_reaches_agent_as_plain_text(media_handler):
    from linebot.v3.webhooks import AudioMessageContent, ContentProvider

    message = AudioMessageContent(
        id="M_AUDIO", duration=4000, contentProvider=ContentProvider(type="line")
    )
    message.type = "audio"
    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="肚子痛的原因是什麼啊",
    ):
        user_text, media_type, image_text = await media_handler._extract_media_text(
            message, "U12345"
        )

    assert user_text == "肚子痛的原因是什麼啊"
    assert media_type == "audio"
    assert image_text == ""


@pytest.mark.asyncio
async def test_uploaded_audio_file_transcript_reaches_agent_as_plain_text(media_handler):
    message = FileMessageContent(id="M_M4A", fileName="question.m4a", fileSize=100)
    message.type = "file"
    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        return_value="我頭痛該怎麼辦",
    ):
        user_text, media_type, _ = await media_handler._extract_media_text(
            message, "U12345"
        )

    assert user_text == "我頭痛該怎麼辦"
    assert media_type == "audio"


# 辨識語音之前就要設好使用者的語言：選台語的才會走台語 STT，其他語言的提示也才送得到
# faster-whisper。以前語言要到 _process_and_reply 才設，辨識時一律是預設的 zh-TW。
async def _handle_voice_and_capture_languages(profiles, detected=None):
    """`detected` 模擬辨識那邊聽出來的語言（見 mutimedia_processor）。"""
    from linebot.v3.webhooks import AudioMessageContent, ContentProvider

    from app.core.user_language import (
        get_request_language,
        get_request_speech_language,
        set_detected_speech_language,
    )

    agent = MagicMock()
    agent.invoke = AsyncMock(return_value={"response": "AI 回覆"})
    history = MagicMock()
    history.load_history = AsyncMock(return_value=[])
    history.save_turn = AsyncMock()
    replier = MagicMock()
    replier.reply = AsyncMock(return_value=True)
    handler = LineMediaHandler(
        agent=agent, history_service=history, user_profile_service=profiles, replier=replier
    )
    seen = {}

    async def _process_media(**_kwargs):
        seen["speech"] = get_request_speech_language()
        seen["text"] = get_request_language()
        set_detected_speech_language(detected)
        return "阿公，你食飽未？"

    event = _event(
        AudioMessageContent(
            id="M_AUDIO", duration=3000, contentProvider=ContentProvider(type="line")
        )
    )
    with patch(
        "app.services.media.mutimedia_processor.media_processor_service.process_media",
        new_callable=AsyncMock,
        side_effect=_process_media,
    ):
        await handler.handle(event)
    return seen, replier.reply.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored,speech,text",
    [("nan-TW", "nan-TW", "zh-TW"), ("vi", "vi", "vi"), (None, "zh-TW", "zh-TW")],
)
async def test_user_language_is_set_before_transcription(stored, speech, text):
    profiles = MagicMock()
    profiles.get_user_profile = AsyncMock(return_value={"settings": {"language": stored}})

    seen, reply_kwargs = await _handle_voice_and_capture_languages(profiles)

    assert seen == {"speech": speech, "text": text}
    # 回覆時文字用文字語言、語音用語音語言
    assert reply_kwargs["language"] == text
    assert reply_kwargs["speech_language"] == speech


@pytest.mark.asyncio
async def test_profile_read_failure_before_transcription_uses_default_language():
    profiles = MagicMock()
    profiles.get_user_profile = AsyncMock(
        side_effect=[RuntimeError("mongo down"), {"settings": {"language": "nan-TW"}}]
    )

    seen, _ = await _handle_voice_and_capture_languages(profiles)

    assert seen == {"speech": "zh-TW", "text": "zh-TW"}


# 講台語就用台語念回去，使用者不必先到設定頁把語言切成台語（設定仍然是預設的華語）。
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored,detected,speech",
    [
        (None, "nan-TW", "nan-TW"),  # 沒設定過，講台語
        ("zh-TW", "nan-TW", "nan-TW"),  # 設華語，這一句講台語
        ("nan-TW", "zh-TW", "zh-TW"),  # 設台語，這一句講華語
        ("nan-TW", None, "nan-TW"),  # 判不出來（非語音路徑）就照設定
    ],
)
async def test_detected_speech_language_decides_voice_reply(stored, detected, speech):
    profiles = MagicMock()
    profiles.get_user_profile = AsyncMock(return_value={"settings": {"language": stored}})

    _, reply_kwargs = await _handle_voice_and_capture_languages(profiles, detected=detected)

    assert reply_kwargs["speech_language"] == speech
    # 文字回覆一律華語：台語只換語音（見 user_language.TAIWANESE_LANGUAGE）。
    assert reply_kwargs["language"] == "zh-TW"


# ---- 看診錄音在聊天室錄（app/services/clinic_transcript/line_flow.py）----


def _audio_event(message):
    event = MagicMock()
    event.message = message
    event.source.user_id = "U1"
    event.reply_token = "tok"
    return event


@pytest.mark.asyncio
async def test_看診錄音流程接手的語音不進_agent(
    mock_agent, mock_history_service, mock_user_profile_service
):
    from linebot.v3.webhooks import AudioMessageContent

    flow = MagicMock()
    flow.handle_audio = AsyncMock(return_value=True)
    handler = LineMediaHandler(
        agent=mock_agent,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        replier=MagicMock(),
        clinic_recording_flow=flow,
    )
    message = AudioMessageContent(id="A1", duration=600_000, contentProvider={"type": "line"})

    await handler.handle(_audio_event(message))

    kwargs = flow.handle_audio.await_args.kwargs
    assert kwargs["message_id"] == "A1" and kwargs["duration_ms"] == 600_000
    assert kwargs["is_file"] is False
    mock_agent.invoke.assert_not_called()


@pytest.mark.asyncio
async def test_看診錄音流程不接手就照常當問題(
    mock_agent, mock_history_service, mock_user_profile_service
):
    from linebot.v3.webhooks import AudioMessageContent

    flow = MagicMock()
    flow.handle_audio = AsyncMock(return_value=False)
    handler = LineMediaHandler(
        agent=mock_agent,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        replier=MagicMock(),
        clinic_recording_flow=flow,
    )
    handler._extract_media_text = AsyncMock(return_value=("血壓多少算高", "audio", ""))
    handler._process_and_reply = AsyncMock()
    message = AudioMessageContent(id="A1", duration=8_000, contentProvider={"type": "line"})

    await handler.handle(_audio_event(message))

    handler._process_and_reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_看診錄音流程壞掉時語音照常被回答(
    mock_agent, mock_history_service, mock_user_profile_service
):
    from linebot.v3.webhooks import AudioMessageContent

    flow = MagicMock()
    flow.handle_audio = AsyncMock(side_effect=RuntimeError("mongo down"))
    handler = LineMediaHandler(
        agent=mock_agent,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        replier=MagicMock(),
        clinic_recording_flow=flow,
    )
    handler._extract_media_text = AsyncMock(return_value=("頭暈", "audio", ""))
    handler._process_and_reply = AsyncMock()
    message = AudioMessageContent(id="A1", duration=8_000, contentProvider={"type": "line"})

    await handler.handle(_audio_event(message))

    handler._process_and_reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_非音檔的檔案不問看診錄音(
    mock_agent, mock_history_service, mock_user_profile_service
):
    flow = MagicMock()
    flow.handle_audio = AsyncMock(return_value=True)
    handler = LineMediaHandler(
        agent=mock_agent,
        history_service=mock_history_service,
        user_profile_service=mock_user_profile_service,
        replier=MagicMock(),
        clinic_recording_flow=flow,
    )
    handler._extract_media_text = AsyncMock(return_value=("報告", "file", ""))
    handler._process_and_reply = AsyncMock()
    message = FileMessageContent(id="F1", fileName="report.pdf", fileSize=5_000_000)

    await handler.handle(_audio_event(message))

    flow.handle_audio.assert_not_called()
