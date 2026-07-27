from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.models.medication import MedicationLog, MedicationReminder
from app.repositories.medication_repository import (
    MedicationLogRepository,
    MedicationReminderRepository,
)


@pytest.fixture()
def override_medication_reminders_col(monkeypatch):
    def _override(col):
        monkeypatch.setattr(
            "app.repositories.medication_repository.MongoDBManager.get_medication_reminders_collection",
            lambda: col,
        )
        return col

    return _override


@pytest.fixture()
def override_medication_logs_col(monkeypatch):
    def _override(col):
        monkeypatch.setattr(
            "app.repositories.medication_repository.MongoDBManager.get_medication_logs_collection",
            lambda: col,
        )
        return col

    return _override


@pytest.mark.asyncio
async def test_create_reminder(override_medication_reminders_col):
    col = MagicMock()
    col.insert_one = AsyncMock(return_value=MagicMock(inserted_id="fake_id"))
    override_medication_reminders_col(col)

    reminder = MedicationReminder(
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        scheduled_time="08:00",
        start_date="2026-07-25",
    )
    result = await MedicationReminderRepository.create_reminder(reminder)

    assert result.creator_user_id == "U_CARE"
    assert result.user_id == "U_PATIENT"
    assert result.start_date == "2026-07-25"
    col.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_reminder_by_id(override_medication_reminders_col):
    col = MagicMock()
    fake_doc = {
        "_id": "R123",
        "creator_user_id": "U_CARE",
        "user_id": "U_PATIENT",
        "slot_type": "morning",
        "scheduled_time": "08:00",
        "start_date": "2026-07-25",
        "enabled": True,
    }
    col.find_one = AsyncMock(return_value=fake_doc)
    override_medication_reminders_col(col)

    res = await MedicationReminderRepository.get_reminder_by_id("R123")
    assert res is not None
    assert res.id == "R123"
    assert res.slot_type == "morning"


@pytest.mark.asyncio
async def test_upsert_log_uses_single_document(override_medication_logs_col):
    col = MagicMock()
    now = datetime.now(tz=timezone.utc)
    col.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    col.find_one = AsyncMock(
        return_value={
            "_id": "L123",
            "reminder_id": "R123",
            "user_id": "U_PATIENT",
            "alert_notify_user_id": "U_CARE",
            "slot_type": "morning",
            "scheduled_at": now,
            "timeout_at": now,
            "status": "pending",
        }
    )
    override_medication_logs_col(col)

    log = MedicationLog(
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=now,
        timeout_at=now,
    )
    result = await MedicationLogRepository.upsert_log(log)

    assert result.id == "L123"
    col.update_one.assert_awaited_once()
    args, kwargs = col.update_one.await_args
    assert args[0] == {"reminder_id": "R123", "scheduled_at": now}
    assert kwargs.get("upsert") is True


@pytest.mark.asyncio
async def test_mark_as_taken(override_medication_logs_col):
    col = MagicMock()
    now = datetime.now(tz=timezone.utc)
    col.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    col.find_one = AsyncMock(
        return_value={
            "_id": "L123",
            "reminder_id": "R123",
            "user_id": "U_PATIENT",
            "alert_notify_user_id": "U_CARE",
            "slot_type": "morning",
            "scheduled_at": now,
            "timeout_at": now,
            "status": "taken",
            "taken_at": now,
        }
    )
    override_medication_logs_col(col)

    log = await MedicationLogRepository.mark_as_taken("L123", taken_at=now)
    assert log is not None
    assert log.status == "taken"
    assert log.taken_at == now
