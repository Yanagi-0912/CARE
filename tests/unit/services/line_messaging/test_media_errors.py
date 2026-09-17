"""媒體辨識失敗要分開講：太大／不支援／服務失敗／真的沒內容；讀取動畫先開。"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from linebot.v3.webhooks import (
    AudioMessageContent,
    ContentProvider,
    DeliveryContext,
    ImageMessageContent,
    MessageEvent,
    UserSource,
)

from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n.messages import _MESSAGES, t
from app.services.line_messaging.handler.media_handler import LineMediaHandler
from app.services.line_messaging.handler.message_handler import LineValidationError
from app.services.media.mutimedia_processor import (
    NO_CONTENT_TEXT,
    MediaServiceUnavailableError,
    MediaTooLargeError,
    MediaUnsupportedError,
)

PROCESS = "app.services.media.mutimedia_processor.media_processor_service.process_media"


def _handler(language="zh-TW", loading=None):
    profiles = MagicMock()
    profiles.get_user_profile = AsyncMock(return_value={"settings": {"language": language}})
    agent = MagicMock()
    agent.invoke = AsyncMock(return_value={"response": "AI 回覆"})
    history = MagicMock()
    history.load_history = AsyncMock(return_value=[])
    history.save_turn = AsyncMock()
    replier = MagicMock()
    replier.reply = AsyncMock(return_value=True)
    return LineMediaHandler(
        agent=agent,
        history_service=history,
        user_profile_service=profiles,
        replier=replier,
        loading_animation_service=loading,
    )


def _image():
    return ImageMessageContent(id="M_IMG", quoteToken="q", contentProvider=ContentProvider(type="line"))


def _event(message):
    return MessageEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZTEST0000000000000000M1",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt",
        source=UserSource(type="user", userId="U12345"),
        message=message,
    )


async def _error_text(handler, message, side_effect):
    with patch(PROCESS, new_callable=AsyncMock, side_effect=side_effect):
        with pytest.raises(LineValidationError) as exc_info:
            await handler.handle(_event(message))
    return str(exc_info.value)


@pytest.mark.asyncio
async def test_too_large_tells_the_limit():
    text = await _error_text(_handler(), _image(), MediaTooLargeError(20 * 1024 * 1024))

    assert text == t("media.too_large", language="zh-TW").format(kind="圖片", limit_mb=10)
    assert "10 MB" in text


@pytest.mark.asyncio
async def test_unsupported_type_is_the_users_problem_not_the_service():
    text = await _error_text(_handler(), _image(), MediaUnsupportedError("bad mime"))

    assert text == t("media.unsupported", language="zh-TW").format(kind="圖片")


@pytest.mark.asyncio
async def test_service_failure_says_try_later_not_retake_the_photo():
    audio = AudioMessageContent(id="M_A", duration=3000, contentProvider=ContentProvider(type="line"))
    text = await _error_text(_handler(), audio, MediaServiceUnavailableError("n8n 502"))

    assert text == t("media.service_unavailable", language="zh-TW").format(kind="語音")
    assert "清晰" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("returned", ["", "   ", NO_CONTENT_TEXT])
async def test_empty_transcript_asks_for_a_clearer_one(returned):
    handler = _handler()
    with patch(PROCESS, new_callable=AsyncMock, return_value=returned):
        with pytest.raises(LineValidationError) as exc_info:
            await handler.handle(_event(_image()))

    assert str(exc_info.value) == t("media.no_content", language="zh-TW").format(kind="圖片")


@pytest.mark.asyncio
async def test_error_text_follows_the_users_language():
    text = await _error_text(_handler(language="ja"), _image(), MediaServiceUnavailableError("x"))

    assert text == t("media.service_unavailable", language="ja").format(
        kind=t("media.kind.image", language="ja")
    )


@pytest.mark.asyncio
async def test_taiwanese_user_gets_chinese_error_text():
    text = await _error_text(_handler(language="nan-TW"), _image(), MediaTooLargeError(1))

    assert text == t("media.too_large", language="zh-TW").format(kind="圖片", limit_mb=10)


def test_media_messages_exist_in_every_language():
    for key in (
        "media.too_large",
        "media.unsupported",
        "media.service_unavailable",
        "media.no_content",
        "media.kind.image",
        "media.kind.audio",
        "media.kind.video",
        "media.kind.file",
    ):
        assert set(_MESSAGES[key]) == set(SUPPORTED_LANGUAGES), key


@pytest.mark.asyncio
async def test_loading_animation_starts_before_extraction():
    order = []
    loading = MagicMock()

    async def _start(user_id, *_a, **_k):
        order.append(("loading", user_id))

    loading.start = AsyncMock(side_effect=_start)
    handler = _handler(loading=loading)

    async def _process(**_k):
        order.append(("extract", None))
        return "辨識結果"

    with patch(PROCESS, new_callable=AsyncMock, side_effect=_process):
        await handler.handle(_event(_image()))

    assert order[0] == ("loading", "U12345")
    assert ("extract", None) in order
