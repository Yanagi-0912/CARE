"""排程器接上用藥提醒拉霸：送 T+0 時挑語氣與催促時機，T+20 沿用同一種語氣。

拉霸出任何狀況（歷史讀不出來、寫入失敗）都退回現行版本照常送出：提醒不能因為
學習機制出錯而送不出去。長輩關掉提醒的那一頓不進拉霸——訊息沒送出去，沒有東西
可學。
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.medication import MedicationLog
from app.services.line_messaging.flex.medication_flex import MedicationListEntry
from app.services.medication.medication_scheduler import (
    MedicationScheduler,
    _TickMedicationNameCache,
)

# 最晚服藥時刻（timeout_anchor_time）08:00；T+0 是飯前那批的 07:30。催促要從
# 最晚時刻起算，所以兩者刻意不同。
ANCHOR = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)

CONTROL_T0 = "請於 30 分鐘內服藥，並點擊下方按鈕確認。"
BRIEF_T0 = "吃藥時間到了，吃完按下面的按鈕。"
FAMILY_T0 = "吃完藥按一下下面的按鈕，家人就知道你吃過了。"
BRIEF_T20 = "還沒按喔，吃完藥記得按下面的按鈕。"


def _mean(alpha, beta):
    return alpha / (alpha + beta)


def _pending_log(log_id="L1", **overrides):
    fields = dict(
        id=log_id,
        reminder_id="REM_1",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=ANCHOR - timedelta(minutes=30),
        urgent_at=ANCHOR + timedelta(minutes=20),
        timeout_at=ANCHOR + timedelta(minutes=30),
        status="pending",
    )
    fields.update(overrides)
    return MedicationLog(**fields)


def _history_favoring(tone, nudge_minutes):
    """其他長輩的紀錄：這個語氣＋時機 30 次都準時按，現行版 30 次都沒按。"""
    on_time = ANCHOR + timedelta(minutes=5)
    wins = [
        _pending_log(
            f"W{i}", user_id="U_OTHER", status="taken", taken_at=on_time,
            reminder_tone=tone, nudge_minutes=nudge_minutes,
        )
        for i in range(30)
    ]
    losses = [
        _pending_log(
            f"X{i}", user_id="U_OTHER", status="missed",
            reminder_tone="control", nudge_minutes=20,
        )
        for i in range(30)
    ]
    return wins + losses


def _cache(*logs):
    """預先填好藥名查表，不發查詢（寫法同 test_medication_scheduler.py）。"""
    cache = _TickMedicationNameCache(list(logs))
    cache._entries_by_log_id = {
        log.id: [MedicationListEntry(name="脈優", image_url=None)] for log in logs
    }
    return cache


def _rendered(replier):
    return str(replier.push_flex.call_args[0][1].contents.to_dict())


@pytest.fixture()
def replier():
    replier = MagicMock()
    replier.push_flex = AsyncMock(return_value=True)
    return replier


@pytest.fixture()
def profiles():
    service = MagicMock()
    service.get_user_profile = AsyncMock(return_value={"name": "李老先生"})
    return service


@pytest.fixture()
def log_repository():
    repo = MagicMock()
    repo.list_active_reminders_up_to_time = AsyncMock(return_value=[])
    repo.list_pending_patient_reminders = AsyncMock(return_value=[])
    repo.list_pending_urgent_reminders = AsyncMock(return_value=[])
    repo.list_pending_caregiver_alerts = AsyncMock(return_value=[])
    repo.claim_patient_reminder = AsyncMock(return_value=True)
    repo.release_patient_reminder = AsyncMock(return_value=True)
    repo.list_variant_outcomes = AsyncMock(return_value=[])
    # 照實模擬「寫入後讀回資料庫那一份」：回傳帶著這次選項的紀錄。
    repo.assign_reminder_variant = AsyncMock(
        side_effect=lambda log_id, *, tone, nudge_minutes, urgent_at: _pending_log(
            log_id, reminder_tone=tone, nudge_minutes=nudge_minutes, urgent_at=urgent_at
        )
    )
    return repo


@pytest.fixture()
def scheduler(replier, profiles, log_repository):
    reminders = MagicMock()
    reminders.list_active_reminders_up_to_time = AsyncMock(return_value=[])
    reminders.find_by_ids = AsyncMock(return_value=[])
    medications = MagicMock()
    medications.find_active_by_ids = AsyncMock(return_value=[])
    return MedicationScheduler(
        replier=replier,
        user_profile_service=profiles,
        reminder_repository=reminders,
        log_repository=log_repository,
        medication_repository=medications,
        variant_sample=_mean,
    )


# ── T+0：挑選、寫入、照挑到的送 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_t0_records_the_choice_and_moves_the_nudge_from_the_latest_dose_time(
    scheduler, replier, log_repository
):
    log = _pending_log()

    sent = await scheduler._send_patient_reminder(
        log, _cache(log), _history_favoring("brief", 10)
    )

    assert sent is True
    log_repository.assign_reminder_variant.assert_awaited_once_with(
        "L1", tone="brief", nudge_minutes=10, urgent_at=ANCHOR + timedelta(minutes=10)
    )
    assert BRIEF_T0 in _rendered(replier)
    assert CONTROL_T0 not in _rendered(replier)


@pytest.mark.asyncio
async def test_t0_retry_reuses_the_tone_already_stored(scheduler, replier, log_repository):
    """推播失敗後下一輪重送：選項已經寫在紀錄上，不能重挑。"""
    log = _pending_log(reminder_tone="family", nudge_minutes=15)

    await scheduler._send_patient_reminder(log, _cache(log), _history_favoring("brief", 10))

    log_repository.assign_reminder_variant.assert_not_awaited()
    assert FAMILY_T0 in _rendered(replier)


@pytest.mark.asyncio
async def test_t0_uses_whatever_the_database_kept(scheduler, replier, log_repository):
    """另一個實例先寫了 family：這次挑到 brief，但卡片照資料庫那一份送。"""
    log_repository.assign_reminder_variant = AsyncMock(
        return_value=_pending_log(reminder_tone="family", nudge_minutes=20)
    )
    log = _pending_log()

    await scheduler._send_patient_reminder(log, _cache(log), _history_favoring("brief", 10))

    assert FAMILY_T0 in _rendered(replier)


# ── 拉霸出狀況時照常送 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t0_sends_current_wording_when_history_is_unavailable(
    scheduler, replier, log_repository
):
    log = _pending_log()

    sent = await scheduler._send_patient_reminder(log, _cache(log), None)

    assert sent is True
    log_repository.assign_reminder_variant.assert_not_awaited()
    assert CONTROL_T0 in _rendered(replier)


@pytest.mark.asyncio
async def test_t0_still_sends_when_recording_the_choice_fails(
    scheduler, replier, log_repository
):
    log_repository.assign_reminder_variant = AsyncMock(side_effect=RuntimeError("mongo down"))
    log = _pending_log()

    sent = await scheduler._send_patient_reminder(
        log, _cache(log), _history_favoring("brief", 10)
    )

    assert sent is True
    assert CONTROL_T0 in _rendered(replier)


@pytest.mark.asyncio
async def test_t0_sends_current_wording_when_the_log_cannot_be_read_back(
    scheduler, replier, log_repository
):
    log_repository.assign_reminder_variant = AsyncMock(return_value=None)
    log = _pending_log()

    await scheduler._send_patient_reminder(log, _cache(log), _history_favoring("brief", 10))

    assert CONTROL_T0 in _rendered(replier)


@pytest.mark.asyncio
async def test_opted_out_user_is_not_entered_into_the_bandit(
    scheduler, replier, profiles, log_repository
):
    profiles.get_user_profile = AsyncMock(
        return_value={"name": "李老先生", "settings": {"notify_reminder": False}}
    )
    log = _pending_log()

    sent = await scheduler._send_patient_reminder(
        log, _cache(log), _history_favoring("brief", 10)
    )

    assert sent is True
    log_repository.assign_reminder_variant.assert_not_awaited()
    replier.push_flex.assert_not_awaited()


# ── T+20：沿用同一種語氣 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_urgent_reminder_uses_the_tone_chosen_at_t0(scheduler, replier):
    log = _pending_log(reminder_tone="brief", nudge_minutes=10)

    await scheduler._send_urgent_reminder(log, _cache(log))

    assert BRIEF_T20 in _rendered(replier)


# ── 每一輪只讀一次歷史 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_ticks_loads_history_once_for_all_t0_reminders(
    scheduler, replier, log_repository
):
    log_repository.list_pending_patient_reminders.return_value = [
        _pending_log("L1"),
        _pending_log("L2"),
    ]

    await scheduler.process_ticks(now=ANCHOR)

    log_repository.list_variant_outcomes.assert_awaited_once()
    assert replier.push_flex.await_count == 2


@pytest.mark.asyncio
async def test_process_ticks_skips_the_history_query_when_nothing_is_due(
    scheduler, log_repository
):
    await scheduler.process_ticks(now=ANCHOR)

    log_repository.list_variant_outcomes.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_ticks_sends_current_wording_when_the_history_query_fails(
    scheduler, replier, log_repository
):
    log_repository.list_variant_outcomes = AsyncMock(side_effect=RuntimeError("mongo down"))
    log_repository.list_pending_patient_reminders.return_value = [_pending_log()]

    await scheduler.process_ticks(now=ANCHOR)

    log_repository.assign_reminder_variant.assert_not_awaited()
    assert CONTROL_T0 in _rendered(replier)
