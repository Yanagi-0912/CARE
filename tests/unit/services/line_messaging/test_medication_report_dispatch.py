"""聊天回報服藥的兩顆按鈕：「記錯了」與反問時挑哪一頓。

以最小替身直接驗 `_dispatch_postback` 的分支——要證明的只有參數有沒有正確
傳到服務層，以及回的是卡片還是純文字。
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.i18n.messages import t
from app.models.medication import TAIPEI_TZ
from app.services.line_messaging.dispatcher.dispatcher import (
    LineEventDispatcher,
    _report_moment,
)


class FakeReportService:
    def __init__(self, bubble=None, undo_text="已取消", confirm_text="{}"):
        self.undo_calls = []
        self.confirm_calls = []
        self._bubble = bubble
        self._undo_text = undo_text
        self._confirm_text = confirm_text

    async def undo(self, asker_id, log_id, *, language=None):
        self.undo_calls.append((asker_id, log_id, language))
        return self._bubble, self._undo_text

    async def confirm_slot(self, asker_id, log_id, moment, *, language=None):
        self.confirm_calls.append((asker_id, log_id, moment, language))
        return self._confirm_text


def _dispatcher(report_service=None):
    message_handler = MagicMock()
    message_handler._user_profile_service = None
    return LineEventDispatcher(
        message_handler=message_handler,
        media_handler=MagicMock(),
        location_handler=MagicMock(),
        facility_detail_handler=MagicMock(),
        replier=MagicMock(reply=AsyncMock(), reply_flex=AsyncMock()),
        medication_report_service=report_service,
    )


def _postback(data: str):
    return SimpleNamespace(reply_token="tok", postback=SimpleNamespace(data=data))


# ── 記錯了 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_undo_postback_reverts_and_replies_with_a_card():
    bubble = {"type": "bubble", "body": {"type": "box", "layout": "vertical", "contents": []}}
    service = FakeReportService(bubble=bubble, undo_text="已取消早 08:00 的服藥記錄")
    dispatcher = _dispatcher(service)

    await dispatcher._dispatch_postback(
        _postback("action=undo_medication_report&log_id=L1"), "U1", None
    )

    assert service.undo_calls == [("U1", "L1", "zh-TW")]
    dispatcher._replier.reply_flex.assert_awaited_once()
    dispatcher._replier.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_undo_postback_falls_back_to_text_when_there_is_nothing_to_undo():
    service = FakeReportService(bubble=None, undo_text=t("medreport.undo_failed", "zh-TW"))
    dispatcher = _dispatcher(service)

    await dispatcher._dispatch_postback(
        _postback("action=undo_medication_report&log_id=L1"), "U1", None
    )

    dispatcher._replier.reply_flex.assert_not_awaited()
    assert dispatcher._replier.reply.await_args.kwargs["message_text"] == t(
        "medreport.undo_failed", "zh-TW"
    )


@pytest.mark.asyncio
async def test_undo_postback_without_log_id_does_nothing():
    service = FakeReportService()
    dispatcher = _dispatcher(service)

    await dispatcher._dispatch_postback(
        _postback("action=undo_medication_report"), "U1", None
    )

    assert service.undo_calls == []
    dispatcher._replier.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_undo_postback_without_the_service_configured_does_nothing():
    dispatcher = _dispatcher(None)
    await dispatcher._dispatch_postback(
        _postback("action=undo_medication_report&log_id=L1"), "U1", None
    )
    dispatcher._replier.reply.assert_not_awaited()


# ── 是哪一頓 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slot_postback_confirms_the_chosen_log_at_the_stated_time():
    """使用者在反問之前就講了「12 點吃的」，按下按鈕的現在不是那個事實。"""
    service = FakeReportService()
    dispatcher = _dispatcher(service)

    await dispatcher._dispatch_postback(
        _postback("action=report_medication_slot&log_id=L1&at=12:00"), "U1", None
    )

    (asker, log_id, moment, language) = service.confirm_calls[0]
    assert (asker, log_id, language) == ("U1", "L1", "zh-TW")
    assert (moment.hour, moment.minute) == (12, 0)


@pytest.mark.asyncio
async def test_slot_postback_without_log_id_does_nothing():
    service = FakeReportService()
    dispatcher = _dispatcher(service)

    await dispatcher._dispatch_postback(
        _postback("action=report_medication_slot&at=12:00"), "U1", None
    )

    assert service.confirm_calls == []


# ── 按鈕帶回來的時刻 ────────────────────────────────────────────────

NOW = datetime(2026, 9, 23, 12, 36, tzinfo=TAIPEI_TZ)


def test_report_moment_parses_hhmm_into_today_taipei():
    assert _report_moment("08:30", NOW) == datetime(2026, 9, 23, 8, 30, tzinfo=TAIPEI_TZ)


@pytest.mark.parametrize("value", ["", "abc", "25:00", "12:99", "1200", "12:0:0"])
def test_report_moment_falls_back_to_now_on_anything_unparseable(value):
    """按鈕上的值是系統自己寫的；走到這裡代表 postback 被改過，退回現在比拒絕處理好。"""
    assert _report_moment(value, NOW) == NOW


def test_report_moment_never_returns_a_future_time():
    """今天還沒到的時刻不可能已經吃過。"""
    assert _report_moment("23:00", NOW) == NOW
