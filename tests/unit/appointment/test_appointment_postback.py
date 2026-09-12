"""LINE 卡片按鈕：postback → AppointmentService → 回覆。"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.i18n import t
from app.repositories.appointment_repository import AppointmentReminderRepository
from app.services.appointment.appointment_service import AppointmentService
from app.services.line_messaging.dispatcher.dispatcher import LineEventDispatcher

from .support import (
    DAUGHTER,
    PATIENT,
    SON,
    STRANGER,
    TPE,
    FakeAuthz,
    FakeCollection,
    FakeProfiles,
    RecordingReplier,
    make_appointment,
    real_authz,
    rendered,
)

DAY_OF = datetime(2026, 9, 15, 8, 40, tzinfo=TPE)


class Env:
    def __init__(self, authz=None):
        col = FakeCollection()
        self.repo = AppointmentReminderRepository(collection_provider=lambda: col)
        self.service = AppointmentService(
            repository=self.repo,
            authorization_service=authz or FakeAuthz(writers=[DAUGHTER, SON]),
            user_profile_service=FakeProfiles(
                {PATIENT: {"name": "王媽媽"}, DAUGHTER: {"name": "王小明"}}
            ),
            clock=lambda: DAY_OF,
        )
        self.replier = RecordingReplier()
        self.dispatcher = LineEventDispatcher(
            message_handler=MagicMock(),
            media_handler=MagicMock(),
            location_handler=MagicMock(),
            facility_detail_handler=MagicMock(),
            replier=self.replier,
            appointment_service=self.service,
        )

    async def press(self, user_id: str, data: str, language: str = "zh-TW") -> None:
        event = SimpleNamespace(reply_token="RT", postback=SimpleNamespace(data=data))
        await self.dispatcher._dispatch_postback(
            event, user_id, {"settings": {"language": language}}
        )


@pytest.fixture()
async def env():
    e = Env()
    e.reminder = await e.repo.create(make_appointment())
    return e


def depart(reminder_id: str) -> str:
    return f"action=appointment_depart&appointment_id={reminder_id}"


def attend(reminder_id: str) -> str:
    return f"action=appointment_attend&appointment_id={reminder_id}"


async def test_patient_presses_depart(env):
    await env.press(PATIENT, depart(env.reminder.id))
    card = rendered(env.replier.flex_replies[-1]["flex_message"])
    assert "已記錄出發" in card
    assert "08:40　由 您 回報" in card
    assert "王媽媽 的門診" not in card  # 本人看到的不需要寫是誰的
    assert (await env.repo.get_by_id(env.reminder.id)).status == "departed"


async def test_family_presses_attend_on_behalf(env):
    await env.press(DAUGHTER, attend(env.reminder.id))
    card = rendered(env.replier.flex_replies[-1]["flex_message"])
    assert "已記錄到診" in card
    assert "王媽媽 的門診" in card
    stored = await env.repo.get_by_id(env.reminder.id)
    assert stored.status == "attended"
    assert stored.attended_by_user_id == DAUGHTER


async def test_second_press_on_another_phone_shows_who_reported_first(env):
    await env.press(DAUGHTER, depart(env.reminder.id))
    await env.press(PATIENT, depart(env.reminder.id))
    card = rendered(env.replier.flex_replies[-1]["flex_message"])
    assert "已記錄出發" in card
    assert "由 王小明 回報" in card


async def test_no_permission_gets_a_text_reply_in_their_language(env):
    await env.press(STRANGER, depart(env.reminder.id), language="en")
    assert env.replier.flex_replies == []
    assert env.replier.replies[-1]["message_text"] == t("appt.error.forbidden_report", "en")
    assert (await env.repo.get_by_id(env.reminder.id)).status == "scheduled"


async def test_depart_after_attend_explains_instead_of_failing(env):
    await env.press(PATIENT, attend(env.reminder.id))
    await env.press(DAUGHTER, depart(env.reminder.id))
    assert env.replier.replies[-1]["message_text"] == "已經回報到診了，不需要再回報出發。"


async def test_deleted_appointment(env):
    await env.repo.delete(env.reminder.id)
    await env.press(PATIENT, attend(env.reminder.id))
    assert env.replier.replies[-1]["message_text"] == t("appt.error.not_found", "zh-TW")


async def test_a_card_left_on_the_phone_after_cancelling(env):
    """取消之後，先前收到的 T-1h 卡片還留在手機上、按鈕仍按得下去：回純文字說明，
    不寫入。`cancelled` 在本版真的會被寫入，這條路徑要走真的取消。"""
    await env.service.cancel(env.reminder.id, DAUGHTER)
    await env.press(PATIENT, depart(env.reminder.id))
    await env.press(DAUGHTER, attend(env.reminder.id), language="en")

    assert env.replier.flex_replies == []
    assert env.replier.replies[0]["message_text"] == "這筆掛號提醒已經取消，無法回報出發或到診。"
    assert env.replier.replies[1]["message_text"] == t("appt.error.cancelled", "en")
    assert (await env.repo.get_by_id(env.reminder.id)).status == "cancelled"


@pytest.mark.parametrize(
    "role,allowed", [("MEMBER", False), ("CAREGIVER", True), ("GUARDIAN", True)]
)
async def test_card_buttons_use_the_strict_check_in_shadow_mode(role, allowed):
    """影子模式下，只有讀取權的 MEMBER 按卡片按鈕也是 403（已拍板）。"""
    env = Env(authz=real_authz(PATIENT, {DAUGHTER: role}, "shadow"))
    reminder = await env.repo.create(make_appointment())

    await env.press(DAUGHTER, attend(reminder.id))

    stored = await env.repo.get_by_id(reminder.id)
    if allowed:
        assert stored.status == "attended"
    else:
        assert env.replier.replies[-1]["message_text"] == "您沒有權限替這位家人回報出發或到診。"
        assert stored.status == "scheduled"


async def test_missing_id_or_service_is_ignored(env):
    await env.press(PATIENT, "action=appointment_depart")
    env.dispatcher._appointment_service = None
    await env.press(PATIENT, depart(env.reminder.id))
    assert env.replier.replies == [] and env.replier.flex_replies == []
