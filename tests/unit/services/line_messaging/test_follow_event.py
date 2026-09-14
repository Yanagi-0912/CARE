from unittest.mock import AsyncMock, MagicMock

import pytest
from linebot.v3.webhooks import DeliveryContext, FollowDetail, FollowEvent, UserSource

from app.i18n import t
from app.services.line_messaging.dispatcher.dispatcher import LineEventDispatcher
from resources.flex_messages.theme import _SIZE_SCALE

LIFF = "https://liff.line.me/1234-abcd"


def _follow_event(*, user_id: str = "U_NEW", unblocked: bool = False) -> FollowEvent:
    return FollowEvent(
        timestamp=1000,
        mode="active",
        webhookEventId="01HZTEST000000000000000001",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="follow_token",
        source=UserSource(type="user", userId=user_id),
        follow=FollowDetail(isUnblocked=unblocked),
    )


def _dispatcher(*, profile=None, line_language=None):
    message_handler = MagicMock()
    message_handler._user_profile_service.get_user_profile = AsyncMock(return_value=profile)
    replier = MagicMock()
    replier.reply = AsyncMock()
    replier.reply_flex = AsyncMock(return_value=True)
    language_service = MagicMock()
    language_service.get_language.return_value = line_language
    dispatcher = LineEventDispatcher(
        message_handler=message_handler,
        media_handler=MagicMock(),
        location_handler=MagicMock(),
        facility_detail_handler=MagicMock(),
        replier=replier,
        line_language_service=language_service,
        liff_url=LIFF,
    )
    return dispatcher, message_handler._user_profile_service, replier, language_service


def _sent_card(replier):
    replier.reply_flex.assert_awaited_once()
    kwargs = replier.reply_flex.await_args.kwargs
    assert kwargs["reply_token"] == "follow_token"
    return kwargs["flex_message"]


@pytest.mark.asyncio
async def test_new_follower_gets_card_in_their_line_app_language():
    dispatcher, _, replier, language_service = _dispatcher(line_language="ja")

    await dispatcher.handle(_follow_event())

    language_service.get_language.assert_called_once_with("U_NEW")
    assert _sent_card(replier).alt_text == t("welcome.title", "ja")


@pytest.mark.asyncio
@pytest.mark.parametrize("line_language", ["zh-CN", None])
async def test_unsupported_or_unknown_language_falls_back_to_zh_tw(line_language):
    dispatcher, _, replier, _ = _dispatcher(line_language=line_language)

    await dispatcher.handle(_follow_event())

    assert _sent_card(replier).alt_text == t("welcome.title", "zh-TW")


@pytest.mark.asyncio
async def test_returning_user_gets_card_in_their_saved_settings():
    """封鎖後再加回的人已有 profile：照 profile 的語言與字級，不再問 LINE。"""
    profile = {"settings": {"language": "en", "font_size": "xlarge"}}
    dispatcher, _, replier, language_service = _dispatcher(profile=profile, line_language="ja")

    await dispatcher.handle(_follow_event(unblocked=True))

    card = _sent_card(replier)
    header = card.to_dict()["contents"]["header"]["contents"][0]
    assert card.alt_text == t("welcome.title", "en")
    assert header["size"] == _SIZE_SCALE["heading"]["xlarge"]
    language_service.get_language.assert_not_called()


@pytest.mark.asyncio
async def test_failure_never_sends_the_generic_error_text():
    dispatcher, profile_service, replier, _ = _dispatcher()
    profile_service.get_user_profile.side_effect = RuntimeError("mongo down")

    await dispatcher.handle(_follow_event())

    replier.reply.assert_not_awaited()
