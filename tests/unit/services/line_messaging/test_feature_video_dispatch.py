"""歡迎卡「看操作示範」：卡片有沒有按鈕、按下去回不回影片。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from linebot.v3.webhooks import DeliveryContext, FollowDetail, FollowEvent, UserSource

from app.i18n import t
from app.services.line_messaging.dispatcher.dispatcher import LineEventDispatcher
from app.services.line_messaging.flex.welcome_flex import feature_video_postback

BASE = "https://care.example.com"


def _dispatcher(*, public_base_url=BASE):
    message_handler = MagicMock()
    message_handler._user_profile_service = None
    replier = MagicMock(reply=AsyncMock(), reply_flex=AsyncMock(return_value=True))
    dispatcher = LineEventDispatcher(
        message_handler=message_handler,
        media_handler=MagicMock(),
        location_handler=MagicMock(),
        facility_detail_handler=MagicMock(),
        replier=replier,
        public_base_url=public_base_url,
    )
    return dispatcher, replier


async def _press(dispatcher, data, profile=None):
    await dispatcher._dispatch_postback(
        SimpleNamespace(reply_token="tok", postback=SimpleNamespace(data=data)), "U1", profile
    )


@pytest.mark.asyncio
async def test_video_button_replies_with_that_pages_video_in_the_users_language():
    dispatcher, replier = _dispatcher()

    await _press(dispatcher, feature_video_postback("media"), {"settings": {"language": "vi"}})

    replier.reply_flex.assert_awaited_once()
    kwargs = replier.reply_flex.await_args.kwargs
    hero = kwargs["flex_message"].to_dict()["contents"]["hero"]
    assert kwargs["reply_token"] == "tok"
    assert hero["url"] == f"{BASE}/api/feature-videos/vi/media.mp4"
    assert kwargs["flex_message"].alt_text == t("feature_video.title", "vi").format(
        section=t("welcome.section.media.title", "vi")
    )


@pytest.mark.asyncio
async def test_unknown_section_is_ignored():
    dispatcher, replier = _dispatcher()

    await _press(dispatcher, feature_video_postback("nope"))

    replier.reply_flex.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_public_base_url_there_is_no_video_to_send():
    dispatcher, replier = _dispatcher(public_base_url="")

    await _press(dispatcher, feature_video_postback("ai"))

    replier.reply_flex.assert_not_awaited()


def _postback_actions(flex_message) -> list[dict]:
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "postback":
                yield node
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item)

    return list(walk(flex_message.to_dict()["contents"]))


@pytest.mark.asyncio
@pytest.mark.parametrize(("public_base_url", "buttons"), [(BASE, 6), ("", 0)])
async def test_welcome_card_shows_video_buttons_only_with_a_public_base_url(public_base_url, buttons):
    dispatcher, replier = _dispatcher(public_base_url=public_base_url)

    await dispatcher.handle(
        FollowEvent(
            timestamp=1000,
            mode="active",
            webhookEventId="01HZTEST000000000000000003",
            deliveryContext=DeliveryContext(isRedelivery=False),
            replyToken="follow_token",
            source=UserSource(type="user", userId="U_NEW"),
            follow=FollowDetail(isUnblocked=False),
        )
    )

    card = replier.reply_flex.await_args.kwargs["flex_message"]
    assert len(_postback_actions(card)) == buttons
