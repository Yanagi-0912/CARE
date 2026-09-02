from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from linebot.v3.messaging import FlexMessage, TextMessage
from linebot.v3.webhooks import (
    DeliveryContext,
    MessageEvent,
    TextMessageContent,
    UserSource,
)

from app.i18n.messages import t
from app.services.line_messaging.handler.message_handler import LineMessageHandler
from app.services.line_messaging.reply.reply import LineReplier
from tests.conftest import fake_line_token_manager


class FakeTTSService:
    """依賴注入用的 fake TTS 服務，記錄呼叫參數供測試驗證。"""

    def __init__(self, result=None, exc: Exception | None = None):
        self._result = result or (b"audio-bytes", "https://example.com/audio.mp3", 1500)
        self._exc = exc
        self.calls: list[dict] = []

    async def synthesize(
        self,
        text: str,
        language: str = "zh-TW",
        voice_rate: str = "normal",
        voice_gender: str = "female",
    ):
        self.calls.append(
            {
                "text": text,
                "language": language,
                "voice_rate": voice_rate,
                "voice_gender": voice_gender,
            }
        )
        if self._exc is not None:
            raise self._exc
        return self._result


@pytest.fixture
def replier():
    return LineReplier(token_manager=fake_line_token_manager("token"), tts_service=None)


async def _send_reply(replier: LineReplier, **kwargs):
    """在 patch 掉 LINE SDK 呼叫的前提下送出一次 reply()，回傳 (ok, messaging_api mock)。"""
    with patch("app.services.line_messaging.reply.reply.Configuration"), patch(
        "app.services.line_messaging.reply.reply.ApiClient"
    ), patch(
        "app.services.line_messaging.reply.reply.MessagingApi"
    ) as mock_messaging_api:
        messaging_api = MagicMock()
        mock_messaging_api.return_value = messaging_api

        ok = await replier.reply(**kwargs)
    return ok, messaging_api


@pytest.mark.asyncio
async def test_reply_location_qr_label_uses_japanese(replier):

    with patch("app.services.line_messaging.reply.reply.Configuration"), patch(
        "app.services.line_messaging.reply.reply.ApiClient"
    ), patch(
        "app.services.line_messaging.reply.reply.MessagingApi"
    ) as mock_messaging_api:
        messaging_api = MagicMock()
        mock_messaging_api.return_value = messaging_api

        ok = await replier.reply(
            reply_token="rt",
            message_text="please share location",
            user_id="U1",
            request_location=True,
            voice_reply_enabled=False,
            language="ja",
        )

    assert ok is True
    reply_req = messaging_api.reply_message.call_args[0][0]
    qr_label = reply_req.messages[0].quick_reply.items[0].action.label
    assert qr_label == t("location.share_qr_label", "ja")


@pytest.mark.asyncio
async def test_reply_passes_language_and_voice_rate_to_tts():
    fake_tts = FakeTTSService()
    replier = LineReplier(token_manager=fake_line_token_manager("token"), tts_service=fake_tts)

    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text="hello",
        user_id="U1",
        voice_reply_enabled=True,
        language="vi",
        voice_rate="fast",
    )

    assert ok is True
    assert fake_tts.calls == [
        {
            "text": "hello",
            "language": "vi",
            "voice_rate": "fast",
            "voice_gender": "female",
        }
    ]

    reply_req = messaging_api.reply_message.call_args[0][0]
    assert len(reply_req.messages) == 2
    assert reply_req.messages[1].original_content_url == "https://example.com/audio.mp3"
    assert reply_req.messages[1].duration == 1500


@pytest.mark.asyncio
async def test_reply_passes_voice_gender_to_tts():
    fake_tts = FakeTTSService()
    replier = LineReplier(token_manager=fake_line_token_manager("token"), tts_service=fake_tts)

    ok, _messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text="hello",
        user_id="U1",
        voice_reply_enabled=True,
        language="vi",
        voice_rate="fast",
        voice_gender="male",
    )

    assert ok is True
    assert fake_tts.calls == [
        {
            "text": "hello",
            "language": "vi",
            "voice_rate": "fast",
            "voice_gender": "male",
        }
    ]


@pytest.mark.asyncio
async def test_reply_defaults_voice_gender_to_female_when_omitted():
    fake_tts = FakeTTSService()
    replier = LineReplier(token_manager=fake_line_token_manager("token"), tts_service=fake_tts)

    ok, _messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text="hello",
        user_id="U1",
        voice_reply_enabled=True,
        language="zh-TW",
        voice_rate="normal",
    )

    assert ok is True
    assert fake_tts.calls[0]["voice_gender"] == "female"


def _text_event(*, user_id: str = "U1") -> MessageEvent:
    return MessageEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZTEST000000000000000000",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt",
        source=UserSource(type="user", userId=user_id),
        message=TextMessageContent(id="M1", text="hello", quoteToken="qt"),
    )


def _build_handler(replier: LineReplier, user_profile: dict | None) -> LineMessageHandler:
    """建立最小可用的 LineMessageHandler，用來驗證 profile -> reply -> synthesize 的完整傳遞路徑。"""
    profile_service = MagicMock()
    profile_service.get_user_profile = AsyncMock(return_value=user_profile)
    history_service = MagicMock()
    history_service.load_history = AsyncMock(return_value=[])
    history_service.save_turn = AsyncMock()
    agent = MagicMock()
    agent.invoke = AsyncMock(return_value={"response": "hello"})
    return LineMessageHandler(
        agent=agent,
        history_service=history_service,
        user_profile_service=profile_service,
        replier=replier,
    )


async def _handle_event(handler: LineMessageHandler) -> None:
    with patch("app.services.line_messaging.reply.reply.Configuration"), patch(
        "app.services.line_messaging.reply.reply.ApiClient"
    ), patch("app.services.line_messaging.reply.reply.MessagingApi") as mock_messaging_api:
        mock_messaging_api.return_value = MagicMock()
        await handler.handle(_text_event())


@pytest.mark.asyncio
async def test_message_handler_passes_voice_gender_from_settings_to_synthesize():
    fake_tts = FakeTTSService()
    replier = LineReplier(token_manager=fake_line_token_manager("token"), tts_service=fake_tts)
    handler = _build_handler(
        replier,
        {"settings": {"voice_reply_enabled": True, "voice_gender": "male"}},
    )

    await _handle_event(handler)

    assert len(fake_tts.calls) == 1
    assert fake_tts.calls[0]["voice_gender"] == "male"


@pytest.mark.asyncio
async def test_message_handler_defaults_voice_gender_to_female_when_missing():
    fake_tts = FakeTTSService()
    replier = LineReplier(token_manager=fake_line_token_manager("token"), tts_service=fake_tts)
    handler = _build_handler(
        replier,
        {"settings": {"voice_reply_enabled": True}},
    )

    await _handle_event(handler)

    assert len(fake_tts.calls) == 1
    assert fake_tts.calls[0]["voice_gender"] == "female"


@pytest.mark.asyncio
async def test_message_handler_settings_voice_gender_takes_precedence_over_top_level():
    fake_tts = FakeTTSService()
    replier = LineReplier(token_manager=fake_line_token_manager("token"), tts_service=fake_tts)
    handler = _build_handler(
        replier,
        {
            "settings": {"voice_reply_enabled": True, "voice_gender": "male"},
            "voice_gender": "female",
        },
    )

    await _handle_event(handler)

    assert len(fake_tts.calls) == 1
    assert fake_tts.calls[0]["voice_gender"] == "male"


@pytest.mark.asyncio
async def test_reply_flex_message_does_not_trigger_tts():
    fake_tts = FakeTTSService()
    replier = LineReplier(token_manager=fake_line_token_manager("token"), tts_service=fake_tts)

    flex_json = (
        '{"type": "flex", "altText": "alt", '
        '"contents": {"type": "bubble", "body": '
        '{"type": "box", "layout": "vertical", "contents": []}}}'
    )

    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text=flex_json,
        user_id="U1",
        voice_reply_enabled=True,
        language="en",
        voice_rate="normal",
    )

    assert ok is True
    assert fake_tts.calls == []
    reply_req = messaging_api.reply_message.call_args[0][0]
    assert len(reply_req.messages) == 1


@pytest.mark.asyncio
async def test_reply_tts_failure_still_sends_text_without_raising():
    fake_tts = FakeTTSService(exc=RuntimeError("synthesis exploded"))
    replier = LineReplier(token_manager=fake_line_token_manager("token"), tts_service=fake_tts)

    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text="hello",
        user_id="U1",
        voice_reply_enabled=True,
        language="zh-TW",
        voice_rate="slow",
    )

    assert ok is True
    assert len(fake_tts.calls) == 1
    reply_req = messaging_api.reply_message.call_args[0][0]
    assert len(reply_req.messages) == 1
    assert reply_req.messages[0].text == "hello"


@pytest.mark.asyncio
async def test_reply_voice_reply_disabled_skips_tts():
    fake_tts = FakeTTSService()
    replier = LineReplier(token_manager=fake_line_token_manager("token"), tts_service=fake_tts)

    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text="hello",
        user_id="U1",
        voice_reply_enabled=False,
        language="zh-TW",
        voice_rate="fast",
    )

    assert ok is True
    assert fake_tts.calls == []
    reply_req = messaging_api.reply_message.call_args[0][0]
    assert len(reply_req.messages) == 1


@pytest.mark.asyncio
async def test_push_text_sends_a_plain_text_message(replier):
    """背景通報不佔 reply token，且給當事人的訊息一律純文字、不含 Markdown。"""
    replier._token_manager.get_token.return_value = "token"

    with patch("app.services.line_messaging.reply.reply.Configuration"), patch(
        "app.services.line_messaging.reply.reply.ApiClient"
    ), patch(
        "app.services.line_messaging.reply.reply.MessagingApi"
    ) as mock_messaging_api:
        messaging_api = MagicMock()
        mock_messaging_api.return_value = messaging_api

        ok = await replier.push_text("U1", "這個名字我查不到")

    assert ok is True
    push_req = messaging_api.push_message.call_args[0][0]
    assert push_req.to == "U1"
    assert push_req.messages[0].text == "這個名字我查不到"


@pytest.mark.asyncio
async def test_push_text_returns_false_without_a_user_id(replier):
    assert await replier.push_text("", "訊息") is False


@pytest.mark.asyncio
async def test_rag_answer_kind_sends_flex(replier):
    from app.core.rag_sources import SourceRef, reset_request_rag_sources, set_request_rag_sources

    token = set_request_rag_sources(
        [SourceRef(index=1, label="食藥署", url="https://www.fda.gov.tw/b")]
    )
    try:
        ok, messaging_api = await _send_reply(
            replier,
            reply_token="rt",
            message_text=(
                f"{t('agent.rag_prefix', 'zh-TW')}\n蜂蜜放室溫即可 [1]。\n\n"
                f"{t('agent.sources_heading', 'zh-TW')}\n"
                "[1] 食藥署：https://www.fda.gov.tw/b"
            ),
            user_id="U1",
            voice_reply_enabled=False,
            answer_kind="rag",
            user_question="蜂蜜怎麼保存？",
        )
    finally:
        reset_request_rag_sources(token)

    assert ok is True
    sent = messaging_api.reply_message.call_args[0][0].messages
    assert isinstance(sent[0], FlexMessage)

    rendered = str(sent[0].contents.to_dict())
    assert "蜂蜜怎麼保存？" in rendered
    assert t("agent.rag_prefix", "zh-TW") not in rendered, "卡片不得含 RAG 前綴"
    assert "https://www.fda.gov.tw/b" in rendered, "來源網址應出現在按鈕的 uri"
    assert rendered.count("https://www.fda.gov.tw/b") == 1, "來源不得同時出現在內文與按鈕"


@pytest.mark.asyncio
async def test_no_answer_kind_still_sends_text(replier):
    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text="一般閒聊回覆。",
        user_id="U1",
        voice_reply_enabled=False,
    )

    assert ok is True
    assert isinstance(
        messaging_api.reply_message.call_args[0][0].messages[0], TextMessage
    )


@pytest.mark.asyncio
async def test_oversized_rag_card_falls_back_to_text(replier):
    """卡片太大時退回純文字，使用者仍拿得到內容。"""
    long_answer = "衛" * 4000

    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text=long_answer,
        user_id="U1",
        voice_reply_enabled=False,
        answer_kind="rag",
        user_question="蜂蜜怎麼保存？",
    )

    assert ok is True
    sent = messaging_api.reply_message.call_args[0][0].messages[0]
    assert isinstance(sent, TextMessage)
    assert sent.text == long_answer


@pytest.mark.asyncio
async def test_builder_failure_falls_back_to_text(replier, monkeypatch):
    """builder 拋例外時退回純文字，不得讓使用者拿到空白回覆。

    這裡 patch 的是本模組自己 import 的 builder（呈現層內部細節），
    不是應用層依賴的注入點，因此不違反「以 DI 傳入 mock」的規則。
    """

    def _boom(*args, **kwargs):
        raise RuntimeError("builder 壞了")

    monkeypatch.setattr(
        "app.services.line_messaging.reply.reply.build_rag_answer_flex", _boom
    )

    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text="蜂蜜放室溫即可。",
        user_id="U1",
        voice_reply_enabled=False,
        answer_kind="rag",
        user_question="蜂蜜怎麼保存？",
    )

    assert ok is True
    assert isinstance(
        messaging_api.reply_message.call_args[0][0].messages[0], TextMessage
    )


@pytest.mark.asyncio
async def test_flex_branch_appends_audio_when_voice_enabled():
    """開了語音回覆的使用者不該在 RAG 回覆上靜默失去語音。"""
    fake_tts = FakeTTSService()
    replier = LineReplier(
        token_manager=fake_line_token_manager("token"), tts_service=fake_tts
    )

    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text=f"{t('agent.rag_prefix', 'zh-TW')}\n蜂蜜放室溫即可。",
        user_id="U1",
        voice_reply_enabled=True,
        answer_kind="rag",
        user_question="蜂蜜怎麼保存？",
    )

    assert ok is True
    sent = messaging_api.reply_message.call_args[0][0].messages
    assert len(sent) == 2
    assert isinstance(sent[0], FlexMessage)
    assert sent[1].original_content_url == "https://example.com/audio.mp3"
    assert fake_tts.calls[0]["text"] == "蜂蜜放室溫即可。", "朗讀的是組卡前的純文字，且不含前綴"


@pytest.mark.asyncio
async def test_flex_branch_skips_audio_when_voice_disabled():
    fake_tts = FakeTTSService()
    replier = LineReplier(
        token_manager=fake_line_token_manager("token"), tts_service=fake_tts
    )

    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text="蜂蜜放室溫即可。",
        user_id="U1",
        voice_reply_enabled=False,
        answer_kind="rag",
        user_question="蜂蜜怎麼保存？",
    )

    assert ok is True
    assert len(messaging_api.reply_message.call_args[0][0].messages) == 1
    assert fake_tts.calls == []


@pytest.mark.asyncio
async def test_tool_flex_still_has_no_audio():
    """工具自產的 Flex（判定卡、官網卡）行為不變：本次不為它們新增語音。"""
    fake_tts = FakeTTSService()
    replier = LineReplier(
        token_manager=fake_line_token_manager("token"), tts_service=fake_tts
    )

    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text='{"type": "flex", "altText": "判定", "contents": {"type": "bubble"}}',
        user_id="U1",
        voice_reply_enabled=True,
    )

    assert ok is True
    assert len(messaging_api.reply_message.call_args[0][0].messages) == 1
    assert fake_tts.calls == []


@pytest.mark.asyncio
async def test_quick_reply_still_on_last_message(replier):
    """位置 Quick Reply 掛在最後一則的既有行為不得改變。"""
    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text="請分享你的位置。",
        user_id="U1",
        request_location=True,
        voice_reply_enabled=False,
    )

    assert ok is True
    sent = messaging_api.reply_message.call_args[0][0].messages
    assert sent[-1].quick_reply is not None
