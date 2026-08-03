from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.models.medication import MedicationLog, MedicationReminder
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

