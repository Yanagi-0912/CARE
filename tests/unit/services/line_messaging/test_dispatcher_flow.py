"""dispatcher 的進站規則：來源過濾、追蹤旗標、每人一把鎖、except 分支不再炸。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from linebot.v3.webhooks import (
    DeliveryContext,
    FollowDetail,
    FollowEvent,
    GroupSource,
    MessageEvent,
    TextMessageContent,
    UnfollowEvent,
    UserSource,
)

from app.i18n.messages import t
from app.services.line_messaging.dispatcher.dispatcher import LineEventDispatcher
from app.services.line_messaging.handler.message_handler import LineValidationError


def _text_event(user_id="U1", text="hi", *, source=None, reply_token="rt"):
    return MessageEvent(
        timestamp=1000,
        mode="active",
        webhookEventId="01HZTEST0000000000000000AA",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken=reply_token,
        source=source or UserSource(type="user", userId=user_id),
        message=TextMessageContent(id="M1", text=text, quoteToken="q"),
    )


def _unfollow_event(user_id="U1"):
    return UnfollowEvent(
        timestamp=1000,
        mode="active",
        webhookEventId="01HZTEST0000000000000000AB",
        deliveryContext=DeliveryContext(isRedelivery=False),
        source=UserSource(type="user", userId=user_id),
    )


def _follow_event(user_id="U1"):
    return FollowEvent(
        timestamp=1000,
        mode="active",
        webhookEventId="01HZTEST0000000000000000AC",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="follow_token",
        source=UserSource(type="user", userId=user_id),
        follow=FollowDetail(isUnblocked=True),
    )


def _dispatcher(*, message_handle=None, profile=None, profile_error=None, set_following=None):
    message_handler = MagicMock()
    message_handler.handle = AsyncMock(side_effect=message_handle)
    if profile_error is not None:
        message_handler._user_profile_service.get_user_profile = AsyncMock(
            side_effect=profile_error
        )
    else:
        message_handler._user_profile_service.get_user_profile = AsyncMock(
            return_value=profile
        )
    replier = MagicMock()
    replier.reply = AsyncMock(return_value=True)
    replier.reply_flex = AsyncMock(return_value=True)
    dispatcher = LineEventDispatcher(
        message_handler=message_handler,
        media_handler=MagicMock(),
        location_handler=MagicMock(),
        facility_detail_handler=MagicMock(),
        replier=replier,
        set_following=set_following,
    )
    return dispatcher, message_handler, replier


# --- 來源 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_messages_are_ignored_without_loading_a_profile(caplog):
    dispatcher, message_handler, replier = _dispatcher()
    event = _text_event(source=GroupSource(type="group", groupId="G1", userId="U1"))

    with caplog.at_level("INFO"):
        await dispatcher.handle(event)

    message_handler.handle.assert_not_awaited()
    message_handler._user_profile_service.get_user_profile.assert_not_awaited()
    replier.reply.assert_not_awaited()
    assert any("非一對一" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_missing_reply_token_still_handles_the_event():
    """replier 會自動改走 push，缺 token 不該把事件丟掉。"""
    dispatcher, message_handler, _ = _dispatcher()

    await dispatcher.handle(_text_event(reply_token=""))

    message_handler.handle.assert_awaited_once()


# --- 追蹤旗標 -----------------------------------------------------------


@pytest.mark.asyncio
async def test_unfollow_marks_profile_not_following_and_does_not_reply(caplog):
    set_following = AsyncMock(return_value=True)
    dispatcher, _, replier = _dispatcher(set_following=set_following)

    with caplog.at_level("WARNING"):
        await dispatcher.handle(_unfollow_event("U_BYE"))

    set_following.assert_awaited_once()
    args = set_following.await_args.args
    assert args[0] == "U_BYE" and args[1] is False
    assert args[2].tzinfo is not None
    replier.reply.assert_not_awaited()
    replier.reply_flex.assert_not_awaited()
    # 不是格式錯誤，不該有「缺 reply_token」的警告
    assert not any("reply_token" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_follow_marks_profile_following_again_before_welcome_card():
    set_following = AsyncMock(return_value=True)
    dispatcher, _, replier = _dispatcher(set_following=set_following)

    await dispatcher.handle(_follow_event("U_BACK"))

    args = set_following.await_args.args
    assert args[0] == "U_BACK" and args[1] is True
    replier.reply_flex.assert_awaited_once()


@pytest.mark.asyncio
async def test_following_flag_failure_does_not_block_welcome_card():
    set_following = AsyncMock(side_effect=RuntimeError("mongo down"))
    dispatcher, _, replier = _dispatcher(set_following=set_following)

    await dispatcher.handle(_follow_event())

    replier.reply_flex.assert_awaited_once()


# --- 每人一把鎖 ---------------------------------------------------------


@pytest.mark.asyncio
async def test_same_user_events_run_in_order_while_other_users_run_in_parallel():
    order = []
    gate = asyncio.Event()

    async def _handle(event):
        order.append(("start", event.source.user_id, event.message.text))
        if event.message.text == "slow":
            await gate.wait()
        order.append(("end", event.source.user_id, event.message.text))

    dispatcher, _, _ = _dispatcher(message_handle=_handle)

    first = asyncio.create_task(dispatcher.handle(_text_event("U1", "slow")))
    await asyncio.sleep(0)
    second = asyncio.create_task(dispatcher.handle(_text_event("U1", "second")))
    other = asyncio.create_task(dispatcher.handle(_text_event("U2", "other")))
    await asyncio.sleep(0.02)

    # U2 不用等 U1；U1 的第二句在第一句放行前不能開始
    assert ("end", "U2", "other") in order
    assert ("start", "U1", "second") not in order

    gate.set()
    await asyncio.gather(first, second, other)

    u1 = [entry for entry in order if entry[1] == "U1"]
    assert u1 == [
        ("start", "U1", "slow"),
        ("end", "U1", "slow"),
        ("start", "U1", "second"),
        ("end", "U1", "second"),
    ]
    assert dispatcher._user_locks == {}  # 沒人在用就釋放，dict 不會長大


@pytest.mark.asyncio
async def test_lock_is_released_when_handler_raises():
    dispatcher, _, _ = _dispatcher(message_handle=RuntimeError("boom"))

    await dispatcher.handle(_text_event("U1"))

    assert dispatcher._user_locks == {}


# --- except 分支 ---------------------------------------------------------


@pytest.mark.asyncio
async def test_language_is_resolved_once_up_front_and_reused_on_error():
    profile = {"settings": {"language": "ja"}}
    dispatcher, message_handler, replier = _dispatcher(
        message_handle=RuntimeError("boom"), profile=profile
    )

    await dispatcher.handle(_text_event())

    message_handler._user_profile_service.get_user_profile.assert_awaited_once_with("U1")
    kwargs = replier.reply.await_args.kwargs
    assert kwargs["language"] == "ja"
    assert kwargs["message_text"] == t("line.fallback_process_error", language="ja")


@pytest.mark.asyncio
async def test_profile_lookup_failure_still_sends_error_in_default_language():
    """Mongo 掛掉時 handler 失敗、語言也查不到：仍要送出預設語言的錯誤說明。"""
    dispatcher, _, replier = _dispatcher(
        message_handle=RuntimeError("mongo"), profile_error=RuntimeError("mongo")
    )

    await dispatcher.handle(_text_event())

    kwargs = replier.reply.await_args.kwargs
    assert kwargs["language"] == "zh-TW"
    assert kwargs["message_text"] == t("line.fallback_process_error", language="zh-TW")


@pytest.mark.asyncio
async def test_validation_error_text_is_sent_verbatim():
    dispatcher, _, replier = _dispatcher(message_handle=LineValidationError("檔案太大"))

    await dispatcher.handle(_text_event())

    assert replier.reply.await_args.kwargs["message_text"] == "檔案太大"


@pytest.mark.asyncio
async def test_except_branch_never_raises_even_if_reply_fails():
    dispatcher, _, replier = _dispatcher(message_handle=RuntimeError("boom"))
    replier.reply = AsyncMock(side_effect=RuntimeError("line down"))

    await dispatcher.handle(_text_event())  # 不會 raise
