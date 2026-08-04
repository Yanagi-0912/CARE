from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.models.medication import TAIPEI_TZ, MedicationLog, MedicationReminder
from app.services.medication.medication_scheduler import MedicationScheduler


@pytest.fixture()
def mock_replier():
    replier = MagicMock()
    replier.push_flex = AsyncMock(return_value=True)
    return replier


@pytest.fixture()
def mock_user_profile_service():
    service = MagicMock()
    service.get_user_profile = AsyncMock(return_value={"name": "李老先生"})
    return service


@pytest.fixture()
def scheduler(mock_replier, mock_user_profile_service):
    return MedicationScheduler(
        replier=mock_replier,
        user_profile_service=mock_user_profile_service,
        check_interval_seconds=60,
    )


@pytest.mark.asyncio
async def test_process_ticks_t0_initial_reminder(scheduler, mock_replier):
    now = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
    fake_reminder = MedicationReminder(
        id="REM_1",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        scheduled_time="08:00",
        start_date="2026-07-29",
    )
    fake_log = MedicationLog(
        id="LOG_1",
        reminder_id="REM_1",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=now,
        timeout_at=now,
        status="pending",
        patient_reminder_sent=False,
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[fake_reminder],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
        return_value=fake_log,
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_patient_reminders",
        new_callable=AsyncMock,
        return_value=[fake_log],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.mark_patient_reminder_sent",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_mark, patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_urgent_reminders",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_caregiver_alerts",
        new_callable=AsyncMock,
        return_value=[],
    ):
        await scheduler.process_ticks(now=now)

        mock_replier.push_flex.assert_awaited_once()
        call_args = mock_replier.push_flex.call_args[0]
        assert call_args[0] == "U_PATIENT"
        assert call_args[1].type == "flex"
        mock_mark.assert_awaited_once_with("LOG_1")


@pytest.mark.asyncio
async def test_process_ticks_t20_urgent_reminder(scheduler, mock_replier):
    now = datetime(2026, 7, 29, 8, 21, tzinfo=timezone.utc)
    scheduled_at = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
    fake_log = MedicationLog(
        id="LOG_1",
        reminder_id="REM_1",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=scheduled_at,
        timeout_at=scheduled_at,
        status="pending",
        patient_reminder_sent=True,
        urgent_reminder_sent=False,
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_patient_reminders",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_urgent_reminders",
        new_callable=AsyncMock,
        return_value=[fake_log],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_caregiver_alerts",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.mark_patient_urgent_reminder_sent",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_mark:
        await scheduler.process_ticks(now=now)

        mock_replier.push_flex.assert_awaited_once()
        call_args = mock_replier.push_flex.call_args[0]
        assert call_args[0] == "U_PATIENT"
        mock_mark.assert_awaited_once_with("LOG_1")


@pytest.mark.asyncio
async def test_process_ticks_t30_caregiver_alert(scheduler, mock_replier):
    now = datetime(2026, 7, 29, 8, 31, tzinfo=timezone.utc)
    scheduled_at = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
    timeout_at = datetime(2026, 7, 29, 8, 30, tzinfo=timezone.utc)
    fake_log = MedicationLog(
        id="LOG_1",
        reminder_id="REM_1",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=scheduled_at,
        timeout_at=timeout_at,
        status="pending",
        patient_reminder_sent=True,
        urgent_reminder_sent=True,
        caregiver_alert_sent=False,
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_patient_reminders",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_urgent_reminders",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_caregiver_alerts",
        new_callable=AsyncMock,
        return_value=[fake_log],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.mark_caregiver_alert_sent",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_mark:
        await scheduler.process_ticks(now=now)

        mock_replier.push_flex.assert_awaited_once()
        call_args = mock_replier.push_flex.call_args[0]
        assert call_args[0] == "U_CARE"  # Sent to caregiver
        mock_mark.assert_awaited_once_with("LOG_1")


# ── Regression：推播文案的時間必須是台北時間 ──────────────────────────


@pytest.mark.asyncio
async def test_reminder_flex_shows_taipei_time_not_utc(scheduler):
    """
    pymongo 以 naive UTC 讀回 scheduled_at，直接 strftime 會顯示 00:00
    而不是使用者設定的台北 08:00。三個階段的推播文案都必須經過時區轉換。
    """
    now = datetime(2026, 7, 29, 9, 0, tzinfo=TAIPEI_TZ)
    # 台北 08:00 存進 Mongo 再讀回來的樣子
    scheduled_from_db = datetime(2026, 7, 29, 0, 0)
    fake_log = MedicationLog(
        id="LOG_1",
        reminder_id="REM_1",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=scheduled_from_db,
        timeout_at=datetime(2026, 7, 29, 0, 30),
        status="pending",
        patient_reminder_sent=False,
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_patient_reminders",
        new_callable=AsyncMock,
        return_value=[fake_log],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.mark_patient_reminder_sent",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_urgent_reminders",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_caregiver_alerts",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.build_patient_medication_flex"
    ) as mock_build:
        await scheduler.process_ticks(now=now)

        mock_build.assert_called_once()
        assert mock_build.call_args.kwargs["scheduled_time"] == "08:00"


@pytest.mark.asyncio
async def test_caregiver_alert_shows_taipei_time_not_utc(scheduler, mock_replier):
    now = datetime(2026, 7, 29, 9, 0, tzinfo=TAIPEI_TZ)
    fake_log = MedicationLog(
        id="LOG_1",
        reminder_id="REM_1",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=datetime(2026, 7, 29, 0, 0),  # 台北 08:00
        timeout_at=datetime(2026, 7, 29, 0, 30),
        status="pending",
        patient_reminder_sent=True,
        urgent_reminder_sent=True,
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_patient_reminders",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_urgent_reminders",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_caregiver_alerts",
        new_callable=AsyncMock,
        return_value=[fake_log],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.mark_caregiver_alert_sent",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.services.medication.medication_scheduler.build_caregiver_alert_flex"
    ) as mock_build:
        await scheduler.process_ticks(now=now)

        mock_build.assert_called_once()
        assert mock_build.call_args.kwargs["scheduled_time"] == "08:00"


# ── Regression：不為「提醒建立之前」的時段補建 log ──────────────────


@contextmanager
def _only_watch_stage_one():
    """把三個推播查詢都關掉，只觀察階段 1 是否補建 log。"""
    prefix = "app.services.medication.medication_scheduler.MedicationLogRepository"
    with patch(
        f"{prefix}.list_pending_patient_reminders", new_callable=AsyncMock, return_value=[]
    ), patch(
        f"{prefix}.list_pending_urgent_reminders", new_callable=AsyncMock, return_value=[]
    ), patch(
        f"{prefix}.list_pending_caregiver_alerts", new_callable=AsyncMock, return_value=[]
    ):
        yield


@pytest.mark.asyncio
async def test_no_backfill_for_slot_before_reminder_was_created(scheduler):
    """
    20:00 新增一筆早上 08:00 的提醒，不該為今天的 08:00 補建 log。
    否則同一個 tick 會連續發出首刷提醒、T+20 催促與 T+30 家屬逾時警報（全是假的）。
    """
    now = datetime(2026, 7, 29, 20, 0, tzinfo=TAIPEI_TZ)
    reminder = MedicationReminder(
        id="REM_1",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        scheduled_time="08:00",
        start_date="2026-07-29",
        # 提醒是今天 20:00 才建立的（資料庫讀回為 naive UTC 12:00）
        created_at=datetime(2026, 7, 29, 12, 0),
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[reminder],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
    ) as mock_upsert, _only_watch_stage_one():
        await scheduler.process_ticks(now=now)

        mock_upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_backfill_still_happens_for_reminder_created_earlier(scheduler):
    """停機補發的能力要保留：昨天就建立的提醒，今天仍要補建 log。"""
    now = datetime(2026, 7, 29, 20, 0, tzinfo=TAIPEI_TZ)
    reminder = MedicationReminder(
        id="REM_1",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        scheduled_time="08:00",
        start_date="2026-07-28",
        created_at=datetime(2026, 7, 28, 1, 0),  # 昨天建立
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[reminder],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
    ) as mock_upsert, _only_watch_stage_one():
        await scheduler.process_ticks(now=now)

        mock_upsert.assert_awaited_once()
        log_arg = mock_upsert.call_args[0][0]
        assert log_arg.scheduled_at == datetime(2026, 7, 29, 8, 0, tzinfo=TAIPEI_TZ)

