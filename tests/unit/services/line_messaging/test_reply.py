from unittest.mock import MagicMock, patch

import pytest

from app.i18n.messages import t
from app.services.line_messaging.reply.reply import LineReplier


class FakeTTSService:
    """依賴注入用的 fake TTS 服務，記錄呼叫參數供測試驗證。"""

    def __init__(self, result=None, exc: Exception | None = None):
        self._result = result or (b"audio-bytes", "https://example.com/audio.mp3", 1500)
        self._exc = exc
        self.calls: list[dict] = []

    async def synthesize(self, text: str, language: str = "zh-TW", voice_rate: str = "normal"):
        self.calls.append({"text": text, "language": language, "voice_rate": voice_rate})
        if self._exc is not None:
            raise self._exc
        return self._result


@pytest.fixture
def replier():
    return LineReplier(token_manager=MagicMock(), tts_service=None)


async def _send_reply(replier: LineReplier, **kwargs):
    """在 patch 掉 LINE SDK 呼叫的前提下送出一次 reply()，回傳 (ok, messaging_api mock)。"""
    replier._token_manager.get_token.return_value = "token"
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
    replier._token_manager.get_token.return_value = "token"

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
    replier = LineReplier(token_manager=MagicMock(), tts_service=fake_tts)

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
    assert fake_tts.calls == [{"text": "hello", "language": "vi", "voice_rate": "fast"}]

    reply_req = messaging_api.reply_message.call_args[0][0]
    assert len(reply_req.messages) == 2
    assert reply_req.messages[1].original_content_url == "https://example.com/audio.mp3"
    assert reply_req.messages[1].duration == 1500


@pytest.mark.asyncio
async def test_reply_flex_message_does_not_trigger_tts():
    fake_tts = FakeTTSService()
    replier = LineReplier(token_manager=MagicMock(), tts_service=fake_tts)

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
    replier = LineReplier(token_manager=MagicMock(), tts_service=fake_tts)

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
    replier = LineReplier(token_manager=MagicMock(), tts_service=fake_tts)

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
