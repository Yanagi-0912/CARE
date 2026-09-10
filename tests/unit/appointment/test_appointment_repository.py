from datetime import datetime

import pytest

from app.repositories.appointment_repository import AppointmentReminderRepository
from app.repositories.push_claim import MAX_PUSH_ATTEMPTS

from .support import APPOINTMENT_AT, PATIENT, TPE, FakeCollection, make_appointment


def at(hour: int, minute: int, day: int = 15) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=TPE)


@pytest.fixture()
def col():
    return FakeCollection()


@pytest.fixture()
def repo(col):
    return AppointmentReminderRepository(collection_provider=lambda: col)


async def seed(repo, **overrides):
    return await repo.create(make_appointment(**overrides))


async def ids(coro) -> list[str]:
    return [reminder.id for reminder in await coro]


# ── 儲存 ──────────────────────────────────────────────────────────────


async def test_create_stores_utc_with_the_offset_and_explicit_nulls(repo, col):
    saved = await seed(repo)
    raw = col.docs[0]
    assert raw["appointment_at"] == datetime(2026, 9, 15, 1, 30)  # Motor 的 naive UTC
    assert raw["appointment_utc_offset_minutes"] == 480
    assert "doctor_name" in raw and raw["doctor_name"] is None

    loaded = await repo.get_by_id(saved.id)
    assert loaded.appointment_at == APPOINTMENT_AT
    assert loaded.local_appointment_at.isoformat() == "2026-09-15T09:30:00+08:00"


async def test_update_fields_writes_null(repo):
    saved = await seed(repo, doctor_name="王大明")
    updated = await repo.update_fields(saved.id, {"doctor_name": None})
    assert updated.doctor_name is None


async def test_list_by_user_is_sorted_and_the_past_boundary_is_day_end(repo):
    later = await seed(repo, at=datetime(2026, 9, 20, 9, 0, tzinfo=TPE))
    today = await seed(repo, at=datetime(2026, 9, 15, 9, 0, tzinfo=TPE))
    yesterday = await seed(repo, at=datetime(2026, 9, 14, 9, 0, tzinfo=TPE))

    assert await ids(repo.list_by_user(PATIENT)) == [yesterday.id, today.id, later.id]
    # 今天 23:00：今天稍早的門診還在「未來與今天」裡，昨天的不在
    assert await ids(repo.list_by_user(PATIENT, day_end_after=at(23, 0))) == [
        today.id,
        later.id,
    ]


async def test_ensure_indexes(repo, col):
    await repo.ensure_indexes()
    assert {kwargs["name"] for _, kwargs in col.indexes} == {
        "user_appointment_at",
        "status_appointment_at",
        "status_day_end_at",
    }


# ── 三個推播階段的時間窗 ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "now,due",
    [(at(8, 29), False), (at(8, 30), True), (at(9, 29), True), (at(9, 30), False)],
)
async def test_pre_reminder_window_is_from_t_minus_1h_until_t0(repo, now, due):
    reminder = await seed(repo)
    assert (reminder.id in await ids(repo.list_due_pre_reminders(now))) is due


async def test_pre_reminder_skips_someone_who_already_departed(repo):
    await seed(repo, status="departed")
    assert await repo.list_due_pre_reminders(at(8, 30)) == []


@pytest.mark.parametrize(
    "now,due",
    [(at(9, 29), False), (at(9, 30), True), (at(9, 59), True), (at(10, 0), False)],
)
async def test_start_reminder_window_is_from_t0_until_t_plus_30(repo, now, due):
    reminder = await seed(repo)
    assert (reminder.id in await ids(repo.list_due_start_reminders(now))) is due


@pytest.mark.parametrize(
    "now,due",
    [
        (at(9, 59), False),
        (at(10, 0), True),
        (at(23, 59), True),
        (at(0, 0, day=16), False),
    ],
)
async def test_caregiver_alert_window_is_from_t_plus_30_until_day_end(repo, now, due):
    reminder = await seed(repo)
    assert (reminder.id in await ids(repo.list_due_caregiver_alerts(now))) is due


async def test_caregiver_alert_still_fires_after_departing_but_not_after_attending(repo):
    """已拍板：判定是「不是 attended」，不是「不是 departed」。"""
    departed = await seed(repo, status="departed")
    attended = await seed(repo, status="attended")
    due = await ids(repo.list_due_caregiver_alerts(at(10, 0)))
    assert departed.id in due
    assert attended.id not in due


async def test_disabled_reminders_are_never_due(repo):
    await seed(repo, enabled=False)
    assert await repo.list_due_pre_reminders(at(8, 30)) == []
    assert await repo.list_due_start_reminders(at(9, 30)) == []
    assert await repo.list_due_caregiver_alerts(at(10, 0)) == []


# ── 推播權搶佔 ────────────────────────────────────────────────────────


async def test_claim_is_won_only_once(repo):
    reminder = await seed(repo)
    assert await repo.claim_pre_reminder(reminder.id, now=at(8, 30)) is True
    assert await repo.claim_pre_reminder(reminder.id, now=at(8, 30)) is False


async def test_claim_rechecks_status_so_a_just_attended_reminder_is_not_pushed(repo):
    """清單查出來之後、搶佔之前按下「我已到診」：搶佔必須失敗。"""
    reminder = await seed(repo)
    assert reminder.id in await ids(repo.list_due_start_reminders(at(9, 30)))
    await repo.mark_attended(reminder.id, by_user_id=PATIENT, at=at(9, 30))
    assert await repo.claim_start_reminder(reminder.id, now=at(9, 30)) is False
    assert await repo.claim_caregiver_alert(reminder.id, now=at(10, 0)) is False


async def test_claim_pre_reminder_rechecks_departed(repo):
    reminder = await seed(repo)
    await repo.mark_departed(reminder.id, by_user_id=PATIENT, at=at(8, 20))
    assert await repo.claim_pre_reminder(reminder.id, now=at(8, 30)) is False


async def test_release_retries_until_the_shared_attempt_cap(repo):
    reminder = await seed(repo)
    for _ in range(MAX_PUSH_ATTEMPTS - 1):
        assert await repo.claim_pre_reminder(reminder.id, now=at(8, 30))
        assert await repo.release_pre_reminder(reminder.id) is True

    assert await repo.claim_pre_reminder(reminder.id, now=at(8, 30))
    assert await repo.release_pre_reminder(reminder.id) is False  # 放棄
    assert await repo.claim_pre_reminder(reminder.id, now=at(8, 31)) is False
    assert (await repo.get_by_id(reminder.id)).pre_reminder_attempts == MAX_PUSH_ATTEMPTS


# ── 狀態轉移與當日結束 ────────────────────────────────────────────────


async def test_mark_departed_only_from_scheduled(repo):
    reminder = await seed(repo)
    first = await repo.mark_departed(reminder.id, by_user_id=PATIENT, at=at(8, 40))
    assert first.status == "departed"
    assert first.departed_by_user_id == PATIENT
    assert await repo.mark_departed(reminder.id, by_user_id="U_OTHER", at=at(8, 41)) is None
    assert (await repo.get_by_id(reminder.id)).departed_by_user_id == PATIENT


@pytest.mark.parametrize("status,ok", [
    ("scheduled", True),
    ("departed", True),
    ("attended", False),
    ("missed", False),
    ("cancelled", False),
])
async def test_mark_attended_source_states(repo, status, ok):
    reminder = await seed(repo, status=status)
    result = await repo.mark_attended(reminder.id, by_user_id=PATIENT, at=at(9, 35))
    assert (result is not None) is ok


async def test_mark_missed_at_day_end_regardless_of_enabled_but_never_attended(repo):
    scheduled = await seed(repo)
    departed = await seed(repo, status="departed")
    disabled = await seed(repo, enabled=False)
    attended = await seed(repo, status="attended")

    assert await repo.mark_missed(at(23, 59)) == 0
    assert await repo.mark_missed(at(0, 0, day=16)) == 3

    assert (await repo.get_by_id(scheduled.id)).status == "missed"
    assert (await repo.get_by_id(departed.id)).status == "missed"
    assert (await repo.get_by_id(disabled.id)).status == "missed"
    assert (await repo.get_by_id(attended.id)).status == "attended"
