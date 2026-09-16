"""用藥提醒流程的幾個修正，從排程器整輪（process_ticks）的角度釘住：

- 重新啟用一筆規則（enabled_at）不為當天已過去的時段補建漏服；
- 關掉「用藥提醒通知」的人不追蹤：不展開紀錄，已展開的註銷而不是判漏服；
- 搶到推播權之後 task 被取消（CancelledError 不是 Exception）也要還回推播權；
- LINE 429／400 分開處理：額度用完不計次還回並每 tick 一行警告，明確拒絕立刻放棄；
- 送達／略過各寫自己的時刻標記。
"""

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.medication import TAIPEI_TZ, MedicationLog, MedicationReminder
from app.services.line_messaging.send_result import SendOutcome, SendResult
from app.services.medication.medication_scheduler import MedicationScheduler


# ── Fixtures：與 test_medication_scheduler.py 同一套注入慣例 ──────────


def _push_flex_result_via(replier):
    async def _push(user_id, card):
        outcome = await replier.push_flex(user_id, card)
        if isinstance(outcome, SendResult):
            return outcome
        return SendResult.success() if outcome else SendResult(SendOutcome.TRANSIENT)

    return AsyncMock(side_effect=_push)


@pytest.fixture()
def replier():
    replier = MagicMock()
    replier.push_flex = AsyncMock(return_value=True)
    replier.push_flex_result = _push_flex_result_via(replier)
    return replier


@pytest.fixture()
def profiles():
    service = MagicMock()
    service.get_user_profile = AsyncMock(return_value={"name": "李老先生"})
    return service


@pytest.fixture()
def reminder_repository():
    repo = MagicMock()
    repo.list_active_reminders_up_to_time = AsyncMock(return_value=[])
    repo.find_by_ids = AsyncMock(return_value=[])
    return repo


@pytest.fixture()
def log_repository():
    repo = MagicMock()
    repo.upsert_log = AsyncMock(side_effect=lambda log: (log, True))
    repo.cancel_pending_by_reminder_ids = AsyncMock(return_value=0)
    repo.cancel_pending_by_reminder = AsyncMock(return_value=1)
    repo.list_pending_patient_reminders = AsyncMock(return_value=[])
    repo.list_pending_urgent_reminders = AsyncMock(return_value=[])
    repo.list_pending_caregiver_alerts = AsyncMock(return_value=[])
    repo.claim_patient_reminder = AsyncMock(return_value=True)
    repo.claim_patient_urgent_reminder = AsyncMock(return_value=True)
    repo.claim_caregiver_alert = AsyncMock(return_value=True)
    repo.release_patient_reminder = AsyncMock(return_value=True)
    repo.release_patient_urgent_reminder = AsyncMock(return_value=True)
    repo.release_caregiver_alert = AsyncMock(return_value=True)
    repo.mark_stage_sent = AsyncMock(return_value=True)
    repo.mark_stage_skipped = AsyncMock(return_value=True)
    repo.give_up_stage = AsyncMock(return_value=True)
    repo.list_variant_outcomes = AsyncMock(return_value=[])
    repo.assign_reminder_variant = AsyncMock(return_value=None)
    return repo


@pytest.fixture()
def authorization_service():
    service = MagicMock()
    service.notification_recipients = AsyncMock(return_value=["U_CARE"])
    return service


@pytest.fixture()
def scheduler(replier, profiles, reminder_repository, log_repository, authorization_service):
    medications = MagicMock()
    medications.find_active_by_ids = AsyncMock(return_value=[])
    return MedicationScheduler(
        replier=replier,
        user_profile_service=profiles,
        reminder_repository=reminder_repository,
        log_repository=log_repository,
        medication_repository=medications,
        authorization_service=authorization_service,
    )


def _reminder(**overrides) -> MedicationReminder:
    base = dict(
        id="REM_1",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        scheduled_time="08:00",
        start_date="2026-07-20",
        created_at=datetime(2026, 7, 20, 1, 0),  # 上週建立（資料庫讀回為 naive UTC）
    )
    base.update(overrides)
    return MedicationReminder(**base)


def _log(log_id="LOG_1", user_id="U_PATIENT", **overrides) -> MedicationLog:
    scheduled_at = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)  # 台北 08:00
    base = dict(
        id=log_id,
        reminder_id="REM_1",
        user_id=user_id,
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=scheduled_at,
        timeout_at=scheduled_at,
        status="pending",
    )
    base.update(overrides)
    return MedicationLog(**base)


def _profile(**settings):
    return {"name": "李老先生", "settings": settings}


EVENING = datetime(2026, 7, 29, 20, 0, tzinfo=TAIPEI_TZ)
MORNING = datetime(2026, 7, 29, 8, 1, tzinfo=TAIPEI_TZ)


# ── 重新啟用：enabled_at 是展開下界 ──────────────────────────────────


@pytest.mark.asyncio
async def test_reactivated_reminder_does_not_backfill_slots_before_reactivation(
    scheduler, reminder_repository, log_repository
):
    """上週建的提醒今天 20:00 重新打開：早上 08:00 不該被補建成漏服再通知家屬
    ——只看 created_at 擋不住這種情況。"""
    reminder_repository.list_active_reminders_up_to_time.return_value = [
        _reminder(enabled_at=datetime(2026, 7, 29, 12, 0))  # 台北 20:00
    ]

    await scheduler.process_ticks(now=EVENING)

    log_repository.upsert_log.assert_not_awaited()


@pytest.mark.asyncio
async def test_reminder_reactivated_earlier_today_still_expands_later_slots(
    scheduler, reminder_repository, log_repository
):
    """07:00 重新打開，08:00 的時段照常展開——下界是「重新啟用時刻」，不是「當天」。"""
    reminder_repository.list_active_reminders_up_to_time.return_value = [
        _reminder(enabled_at=datetime(2026, 7, 28, 23, 0))  # 台北 07:00
    ]

    await scheduler.process_ticks(now=MORNING)

    log_repository.upsert_log.assert_awaited_once()


@pytest.mark.asyncio
async def test_reminder_without_enabled_at_falls_back_to_created_at(
    scheduler, reminder_repository, log_repository
):
    """本欄位落地前的規則沒有 enabled_at：行為與過去一致，昨天建的今天照常補建。"""
    reminder_repository.list_active_reminders_up_to_time.return_value = [_reminder()]

    await scheduler.process_ticks(now=EVENING)

    log_repository.upsert_log.assert_awaited_once()


@pytest.mark.asyncio
async def test_created_at_still_wins_when_it_is_later_than_enabled_at(
    scheduler, reminder_repository, log_repository
):
    """下界取兩者的較大值：資料異常（enabled_at 早於 created_at）時仍不會為建立
    之前的時段補建。"""
    reminder_repository.list_active_reminders_up_to_time.return_value = [
        _reminder(
            created_at=datetime(2026, 7, 29, 12, 0),  # 今天 20:00 建立
            enabled_at=datetime(2026, 7, 1, 0, 0),
        )
    ]

    await scheduler.process_ticks(now=EVENING)

    log_repository.upsert_log.assert_not_awaited()


# ── 關掉提醒 = 不追蹤 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_opted_out_user_gets_no_log_expanded(
    scheduler, profiles, reminder_repository, log_repository
):
    """不建紀錄，三個階段就沒有東西可推、可判漏服、可通知家屬。"""
    profiles.get_user_profile.return_value = _profile(notify_reminder=False)
    reminder_repository.list_active_reminders_up_to_time.return_value = [_reminder()]

    await scheduler.process_ticks(now=MORNING)

    log_repository.upsert_log.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_lookup_failure_still_expands(
    scheduler, profiles, reminder_repository, log_repository
):
    """讀不到 profile 時視為開啟（與推播階段同一個降級方向）：一次資料庫抖動
    不能讓整批提醒靜默消失。"""
    profiles.get_user_profile.side_effect = RuntimeError("mongo down")
    reminder_repository.list_active_reminders_up_to_time.return_value = [_reminder()]

    await scheduler.process_ticks(now=MORNING)

    log_repository.upsert_log.assert_awaited_once()


@pytest.mark.asyncio
async def test_prefs_are_resolved_once_per_user_per_tick(
    scheduler, profiles, reminder_repository, log_repository
):
    """展開階段逐規則看偏好，同一個人四個時段不該查四次 profile；推播階段再讀
    同一個人也不再查。"""
    # 兩個時段都在補推期限內（不會被判成錯過而觸發停機彙整，那條路徑另外
    # 查一次當事人姓名，不在本測試的範圍）。
    reminder_repository.list_active_reminders_up_to_time.return_value = [
        _reminder(id="REM_1", slot_type="morning", scheduled_time="07:45"),
        _reminder(id="REM_2", slot_type="noon", scheduled_time="07:50"),
    ]
    log_repository.list_pending_patient_reminders.return_value = [_log()]

    await scheduler.process_ticks(now=MORNING)

    assert profiles.get_user_profile.await_count == 1


@pytest.mark.asyncio
async def test_prefs_cache_is_cleared_between_ticks(
    scheduler, profiles, reminder_repository, log_repository
):
    """使用者這一分鐘關掉開關，下一個 tick 就要看到。"""
    reminder_repository.list_active_reminders_up_to_time.return_value = [_reminder()]

    await scheduler.process_ticks(now=MORNING)
    profiles.get_user_profile.return_value = _profile(notify_reminder=False)
    await scheduler.process_ticks(now=MORNING)

    assert profiles.get_user_profile.await_count == 2
    assert log_repository.upsert_log.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "list_name, claim_name",
    [
        ("list_pending_patient_reminders", "claim_patient_reminder"),
        ("list_pending_urgent_reminders", "claim_patient_urgent_reminder"),
        ("list_pending_caregiver_alerts", "claim_caregiver_alert"),
    ],
)
async def test_each_stage_cancels_an_untracked_users_log_instead_of_claiming(
    scheduler, profiles, log_repository, replier, list_name, claim_name
):
    """本判定落地前、或當天中途才關掉的人已經有紀錄：三個階段都在搶佔之前擋
    ——T+30 的搶佔會把 status 改成 missed，不追蹤的人不該留下漏服，家屬也不該
    收到警報。紀錄註銷（cancelled 不進用藥歷史），而不是留在 pending 每個 tick
    再被查到。"""
    profiles.get_user_profile.return_value = _profile(notify_reminder=False, notify_family=True)
    getattr(log_repository, list_name).return_value = [_log()]

    await scheduler.process_ticks(now=EVENING)

    getattr(log_repository, claim_name).assert_not_awaited()
    log_repository.cancel_pending_by_reminder.assert_awaited_once_with("REM_1")
    replier.push_flex.assert_not_awaited()


@pytest.mark.asyncio
async def test_tracked_user_in_same_tick_is_unaffected_by_another_users_opt_out(
    scheduler, profiles, log_repository, replier
):
    settings = {"U_OUT": {"notify_reminder": False}, "U_IN": {}}
    profiles.get_user_profile.side_effect = lambda uid: {
        "name": "x", "settings": settings.get(uid, {})
    }
    log_repository.list_pending_patient_reminders.return_value = [
        _log(log_id="LOG_OUT", user_id="U_OUT"),
        _log(log_id="LOG_IN", user_id="U_IN"),
    ]

    await scheduler.process_ticks(now=MORNING)

    log_repository.claim_patient_reminder.assert_awaited_once_with("LOG_IN")
    assert [c.args[0] for c in replier.push_flex.await_args_list] == ["U_IN"]


# ── 搶佔之後被取消：推播權要還回去 ──────────────────────────────────


@pytest.mark.asyncio
async def test_cancelled_during_push_releases_the_claim_and_propagates(
    scheduler, log_repository, replier
):
    """pod 收到 SIGTERM 時 uvicorn 取消所有 task；`CancelledError` 不是 Exception，
    舊的 `except Exception` 接不到，旗標停在「已送出」而沒有人收到。還回去之後
    取消本身仍要往外傳，不能吞掉。"""
    replier.push_flex.side_effect = asyncio.CancelledError()
    log_repository.list_pending_patient_reminders.return_value = [_log()]

    with pytest.raises(asyncio.CancelledError):
        await scheduler.process_ticks(now=MORNING)

    log_repository.release_patient_reminder.assert_awaited_once_with("LOG_1")
    log_repository.mark_stage_sent.assert_not_awaited()


# ── LINE 回應分類 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delivered_push_writes_the_stage_sent_marker(scheduler, log_repository):
    log_repository.list_pending_patient_reminders.return_value = [_log()]

    await scheduler.process_ticks(now=MORNING)

    log_repository.mark_stage_sent.assert_awaited_once_with("LOG_1", stage="patient_reminder")
    log_repository.release_patient_reminder.assert_not_awaited()


@pytest.mark.asyncio
async def test_quota_exceeded_releases_without_counting_and_warns_once_per_tick(
    scheduler, log_repository, replier, caplog
):
    """429：不是這一則的失敗。不計次還回去（額度恢復就送）、不寫送達標記
    （T+20／T+30 因此不會進行），整個 tick 只印一行彙整警告。"""
    replier.push_flex.return_value = SendResult(SendOutcome.QUOTA_EXCEEDED, 429)
    log_repository.list_pending_patient_reminders.return_value = [
        _log(log_id="LOG_1"), _log(log_id="LOG_2")
    ]

    with caplog.at_level(logging.WARNING):
        await scheduler.process_ticks(now=MORNING)

    for log_id in ("LOG_1", "LOG_2"):
        log_repository.release_patient_reminder.assert_any_await(
            log_id, count_attempt=False, error="quota_exceeded"
        )
    log_repository.mark_stage_sent.assert_not_awaited()
    quota_lines = [r for r in caplog.records if "LINE 額度用完" in r.getMessage()]
    assert len(quota_lines) == 1
    assert "2 則" in quota_lines[0].getMessage()


@pytest.mark.asyncio
async def test_no_quota_warning_when_nothing_was_blocked(scheduler, log_repository, caplog):
    log_repository.list_pending_patient_reminders.return_value = [_log()]

    with caplog.at_level(logging.WARNING):
        await scheduler.process_ticks(now=MORNING)

    assert not [r for r in caplog.records if "LINE 額度用完" in r.getMessage()]


@pytest.mark.asyncio
async def test_rejected_push_gives_up_immediately(scheduler, log_repository, replier):
    """400／404：對象或內容被 LINE 明確拒絕，再送四次也一樣，直接放棄並留下分類。"""
    replier.push_flex.return_value = SendResult(SendOutcome.REJECTED, 400)
    log_repository.list_pending_urgent_reminders.return_value = [
        _log(patient_reminder_sent=True)
    ]

    await scheduler.process_ticks(now=MORNING)

    log_repository.give_up_stage.assert_awaited_once_with(
        "LOG_1", "rejected", stage="urgent_reminder"
    )
    log_repository.release_patient_urgent_reminder.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_failure_keeps_the_counted_retry(scheduler, log_repository, replier):
    replier.push_flex.return_value = SendResult(SendOutcome.TRANSIENT, 503)
    log_repository.list_pending_patient_reminders.return_value = [_log()]

    await scheduler.process_ticks(now=MORNING)

    log_repository.release_patient_reminder.assert_awaited_once_with("LOG_1", error="transient")


@pytest.mark.asyncio
async def test_caregiver_alert_with_no_family_writes_the_skipped_marker(
    scheduler, log_repository, authorization_service
):
    """沒有人該收：寫略過標記而不是送達標記，租約才不會把它當成死掉的搶佔
    一再接手；也不能當成失敗還回去。"""
    authorization_service.notification_recipients.return_value = []
    log_repository.list_pending_caregiver_alerts.return_value = [
        _log(patient_reminder_sent=True)
    ]

    await scheduler.process_ticks(now=MORNING)

    log_repository.mark_stage_skipped.assert_awaited_once_with("LOG_1", stage="caregiver_alert")
    log_repository.mark_stage_sent.assert_not_awaited()
    log_repository.release_caregiver_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_caregiver_alert_all_failed_picks_the_most_retryable_outcome(
    scheduler, log_repository, replier, authorization_service
):
    """兩位家屬一位額度用完、一位被明確拒絕：整則以「還值得重試」為準——
    不能因為其中一位的 400 就放棄另一位。"""
    authorization_service.notification_recipients.return_value = ["U_A", "U_B"]
    replier.push_flex.side_effect = [
        SendResult(SendOutcome.REJECTED, 400),
        SendResult(SendOutcome.QUOTA_EXCEEDED, 429),
    ]
    log_repository.list_pending_caregiver_alerts.return_value = [
        _log(patient_reminder_sent=True)
    ]

    await scheduler.process_ticks(now=MORNING)

    log_repository.release_caregiver_alert.assert_awaited_once_with(
        "LOG_1", count_attempt=False, error="quota_exceeded"
    )
    log_repository.give_up_stage.assert_not_awaited()


@pytest.mark.asyncio
async def test_caregiver_alert_all_rejected_gives_up(
    scheduler, log_repository, replier, authorization_service
):
    authorization_service.notification_recipients.return_value = ["U_A", "U_B"]
    replier.push_flex.return_value = SendResult(SendOutcome.REJECTED, 404)
    log_repository.list_pending_caregiver_alerts.return_value = [
        _log(patient_reminder_sent=True)
    ]

    await scheduler.process_ticks(now=MORNING)

    log_repository.give_up_stage.assert_awaited_once_with(
        "LOG_1", "rejected", stage="caregiver_alert"
    )
