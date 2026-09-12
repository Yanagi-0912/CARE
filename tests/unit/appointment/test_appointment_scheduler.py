"""排程器的端到端測試：真的 repository（接記憶體 collection）＋真的 Flex builder，
只把 LINE 推播、profile 與家庭授權換成替身。斷言的是「誰在幾點收到了什麼」。"""

from datetime import datetime, timezone

import pytest

from app.repositories.appointment_repository import AppointmentReminderRepository
from app.services.appointment.appointment_scheduler import (
    NOTIFICATION_KIND,
    AppointmentScheduler,
)

from .support import (
    DAUGHTER,
    PATIENT,
    SON,
    TPE,
    FakeAuthz,
    FakeCollection,
    FakeProfiles,
    RecordingReplier,
    make_appointment,
    real_authz,
    rendered,
)


def at(hour: int, minute: int, day: int = 15) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=TPE)


DEFAULT_PROFILES = {
    PATIENT: {"name": "王媽媽"},
    DAUGHTER: {"name": "王小明"},
    SON: {"name": "王大華", "settings": {"language": "en"}},
}


class Harness:
    def __init__(self, *, recipients=(DAUGHTER, SON), profiles=None, failing=(), authz=None):
        self.col = FakeCollection()
        self.repo = AppointmentReminderRepository(collection_provider=lambda: self.col)
        self.replier = RecordingReplier(failing)
        self.authz = authz or FakeAuthz(recipients=recipients)
        self.scheduler = AppointmentScheduler(
            replier=self.replier,
            repository=self.repo,
            authorization_service=self.authz,
            user_profile_service=FakeProfiles(profiles or DEFAULT_PROFILES),
        )

    async def seed(self, **overrides):
        return await self.repo.create(make_appointment(**overrides))

    async def tick(self, now: datetime) -> list[tuple[str, str]]:
        before = len(self.replier.pushes)
        await self.scheduler.process_ticks(now=now)
        return [(uid, rendered(flex)) for uid, flex in self.replier.pushes[before:]]


def recipients(pushes) -> list[str]:
    return [uid for uid, _ in pushes]


@pytest.fixture()
def h():
    return Harness()


# ── 三個階段 ──────────────────────────────────────────────────────────


async def test_t_minus_1h_goes_to_patient_and_family_with_the_depart_button(h):
    reminder = await h.seed()
    pushes = await h.tick(at(8, 30))

    assert recipients(pushes) == [PATIENT, DAUGHTER, SON]
    patient_card, daughter_card, son_card = (card for _, card in pushes)
    assert f"action=appointment_depart&appointment_id={reminder.id}" in patient_card
    assert f"action=appointment_depart&appointment_id={reminder.id}" in daughter_card
    assert "門診提醒" in patient_card
    assert "王媽媽 的門診" in daughter_card  # 家屬版寫出是誰的門診
    assert "王媽媽" not in patient_card
    assert "Appointment reminder" in son_card  # 家屬各自的語言
    # 同一個 tick 不會再推一次，下一個 tick 也不會
    assert await h.tick(at(8, 31)) == []


async def test_t0_after_departing_sends_the_attend_button(h):
    reminder = await h.seed(status="departed")
    pushes = await h.tick(at(9, 30))
    assert recipients(pushes) == [PATIENT, DAUGHTER, SON]
    card = pushes[0][1]
    assert "門診時間到了" in card
    assert f"action=appointment_attend&appointment_id={reminder.id}" in card
    assert "appointment_depart" not in card


async def test_t0_without_departing_is_a_nudge_with_both_buttons(h):
    reminder = await h.seed()
    pushes = await h.tick(at(9, 30))
    card = pushes[0][1]
    assert "還沒出發嗎？" in card
    assert f"action=appointment_attend&appointment_id={reminder.id}" in card
    assert f"action=appointment_depart&appointment_id={reminder.id}" in card


async def test_t_plus_30_goes_to_family_only(h):
    await h.seed()
    pushes = await h.tick(at(10, 0))
    assert recipients(pushes) == [DAUGHTER, SON]
    assert "尚未確認到診" in pushes[0][1]


async def test_t_plus_30_after_departing_still_alerts_and_says_when_they_left(h):
    """出發了卻沒到，比從頭沒動作更值得家人關心（已拍板）。"""
    await h.seed(
        status="departed",
        departed_at=at(8, 40),
        departed_by_user_id=PATIENT,
    )
    pushes = await h.tick(at(10, 0))
    assert recipients(pushes) == [DAUGHTER, SON]
    assert "已在 08:40 回報出發" in pushes[0][1]


async def test_attending_cancels_every_remaining_push(h):
    reminder = await h.seed()
    await h.tick(at(8, 30))
    await h.repo.mark_attended(reminder.id, by_user_id=DAUGHTER, at=at(9, 10))

    assert await h.tick(at(9, 30)) == []
    assert await h.tick(at(10, 0)) == []
    assert await h.tick(at(0, 0, day=16)) == []
    assert (await h.repo.get_by_id(reminder.id)).status == "attended"


async def test_end_of_day_marks_missed_without_pushing(h):
    reminder = await h.seed()
    assert await h.tick(at(0, 0, day=16)) == []
    assert (await h.repo.get_by_id(reminder.id)).status == "missed"


async def test_disabled_reminders_push_nothing_but_still_become_missed(h):
    reminder = await h.seed(enabled=False)
    for now in (at(8, 30), at(9, 30), at(10, 0)):
        assert await h.tick(now) == []
    await h.tick(at(0, 0, day=16))
    assert (await h.repo.get_by_id(reminder.id)).status == "missed"


# ── 停機與晚建立 ──────────────────────────────────────────────────────


async def test_restarting_after_downtime_does_not_burst_three_pushes(h):
    """停機跨過 T-1h 與 T+0：重啟的第一個 tick 只該有 T+30 的家屬警報。"""
    await h.seed()
    pushes = dict(await h.tick(at(10, 5)))
    assert list(pushes) == [DAUGHTER, SON]
    assert "尚未確認到診" in pushes[DAUGHTER]
    assert "Arrival not confirmed" in pushes[SON]  # 他設定的是英文
    # 補推的若是 T-1h 或未出發的 T+0，卡片上會有「我已出發」
    assert all("appointment_depart" not in card for card in pushes.values())


async def test_a_reminder_created_20_minutes_before_still_gets_the_depart_card(h):
    await h.seed(created_at=at(9, 10), updated_at=at(9, 10))
    pushes = await h.tick(at(9, 11))
    assert recipients(pushes) == [PATIENT, DAUGHTER, SON]
    assert "一小時" not in pushes[0][1]


# ── 時區 ──────────────────────────────────────────────────────────────


async def test_schedule_uses_the_instant_and_cards_show_local_time(h):
    """UTC 00:30 就是台北 08:30：T-1h 必須在這時候觸發，卡片寫 09:30 不是 01:30。"""
    await h.seed()
    pushes = await h.tick(datetime(2026, 9, 15, 0, 30, tzinfo=timezone.utc))
    card = pushes[0][1]
    assert "9/15（週二）09:30" in card
    assert "01:30" not in card


async def test_naive_now_is_treated_as_utc(h):
    await h.seed()
    assert recipients(await h.tick(datetime(2026, 9, 15, 0, 29))) == []
    assert recipients(await h.tick(datetime(2026, 9, 15, 0, 30))) == [PATIENT, DAUGHTER, SON]


# ── 隱私邊界 ──────────────────────────────────────────────────────────


async def test_no_push_ever_contains_department_doctor_serial_number_or_note(h):
    """已拍板：推播文案不含科別、醫師、看診號。備註同樣只在 LIFF 內。"""
    secrets = ["精神科", "李醫師", "看診號", "A-0777", "記得帶上次的報告"]
    await h.seed(
        department="精神科",
        doctor_name="李醫師",
        serial_number="A-0777",
        note="記得帶上次的報告",
    )
    await h.seed(
        department="精神科",
        doctor_name="李醫師",
        serial_number="A-0777",
        note="記得帶上次的報告",
        status="departed",
        departed_at=at(8, 40),
        departed_by_user_id=PATIENT,
    )
    pushes = []
    for now in (at(8, 30), at(9, 30), at(10, 0)):
        pushes += await h.tick(now)

    assert len(pushes) == 3 + 3 + 3 + 2 + 2  # T-1h 只推 scheduled 那筆
    for _, card in pushes:
        for secret in secrets:
            assert secret not in card
        assert "台大醫院" in card


# ── 收件人 ────────────────────────────────────────────────────────────


async def test_all_three_stages_use_the_same_family_resolver(h):
    await h.seed()
    for now in (at(8, 30), at(9, 30), at(10, 0)):
        await h.tick(now)
    assert h.authz.recipient_calls == [(PATIENT, NOTIFICATION_KIND)] * 3


async def test_each_recipient_decides_for_themselves(h):
    h = Harness(
        profiles={
            PATIENT: {"name": "王媽媽", "settings": {"notify_reminder": False}},
            DAUGHTER: {"name": "王小明"},
            SON: {"name": "王大華", "settings": {"notify_family": False}},
        }
    )
    await h.seed()
    assert recipients(await h.tick(at(8, 30))) == [DAUGHTER]


async def test_family_resolver_failure_still_reaches_the_patient():
    h = Harness(authz=FakeAuthz(fail_recipients=True))
    await h.seed()
    assert recipients(await h.tick(at(8, 30))) == [PATIENT]


async def test_unknown_patient_name_falls_back_in_each_recipients_language():
    h = Harness(profiles={SON: {"settings": {"language": "en"}}})
    await h.seed()
    pushes = dict(await h.tick(at(8, 30)))
    assert "您的家人 的門診" in pushes[DAUGHTER]
    assert "Your family member's appointment" in pushes[SON]


@pytest.mark.parametrize("state", ["enforced", "shadow"])
async def test_family_recipients_are_the_general_writers_in_both_modes(state):
    """MEMBER 只有 GENERAL 讀取權，按不下卡片上的按鈕，所以不收。

    影子模式也一樣（已拍板）：掛號的寫入在影子模式下同樣是嚴格判定，送給 MEMBER
    就是一張按了必定 403 的卡片。
    """
    authz = real_authz(
        PATIENT,
        {DAUGHTER: "CAREGIVER", SON: "MEMBER", "U_GUARDIAN": "GUARDIAN"},
        state,
    )
    h = Harness(authz=authz)
    await h.seed()
    assert recipients(await h.tick(at(8, 30))) == [PATIENT, DAUGHTER, "U_GUARDIAN"]


# ── 推播失敗 ──────────────────────────────────────────────────────────


async def test_partial_failure_is_not_retried_so_nobody_gets_duplicates():
    h = Harness(failing=[SON])
    reminder = await h.seed()
    assert recipients(await h.tick(at(8, 30))) == [PATIENT, DAUGHTER, SON]
    assert await h.tick(at(8, 31)) == []
    assert (await h.repo.get_by_id(reminder.id)).pre_reminder_attempts == 0


async def test_total_failure_releases_the_claim_and_retries():
    h = Harness(failing=[PATIENT, DAUGHTER, SON])
    reminder = await h.seed()
    await h.tick(at(8, 30))
    assert (await h.repo.get_by_id(reminder.id)).pre_reminder_attempts == 1
    h.replier.failing.clear()
    assert recipients(await h.tick(at(8, 31))) == [PATIENT, DAUGHTER, SON]
    assert await h.tick(at(8, 32)) == []
