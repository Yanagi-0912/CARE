from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from pymongo.errors import DuplicateKeyError

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


def _fake_log_doc(now: datetime) -> dict:
    return {
        "_id": "L123",
        "reminder_id": "R123",
        "user_id": "U_PATIENT",
        "alert_notify_user_id": "U_CARE",
        "slot_type": "morning",
        "scheduled_at": now,
        "timeout_at": now,
        "status": "pending",
    }


def _sample_log(now: datetime) -> MedicationLog:
    return MedicationLog(
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=now,
        timeout_at=now,
    )


@pytest.mark.asyncio
async def test_upsert_log_uses_single_document(override_medication_logs_col):
    col = MagicMock()
    now = datetime.now(tz=timezone.utc)
    col.update_one = AsyncMock(return_value=MagicMock(upserted_id="L123"))
    col.find_one = AsyncMock(return_value=_fake_log_doc(now))
    override_medication_logs_col(col)

    result, created = await MedicationLogRepository.upsert_log(_sample_log(now))

    assert result.id == "L123"
    assert created is True
    col.update_one.assert_awaited_once()
    args, kwargs = col.update_one.await_args
    assert args[0] == {"reminder_id": "R123", "scheduled_at": now}
    assert kwargs.get("upsert") is True


@pytest.mark.asyncio
async def test_upsert_log_reports_not_created_when_already_exists(
    override_medication_logs_col,
):
    """
    created 是「錯過的時段要不要通知家屬」的唯一依據——已存在的 log 必須回報 False，
    否則每個 tick 都會重新發一次彙整通知。
    """
    col = MagicMock()
    now = datetime.now(tz=timezone.utc)
    col.update_one = AsyncMock(return_value=MagicMock(upserted_id=None))
    col.find_one = AsyncMock(return_value=_fake_log_doc(now))
    override_medication_logs_col(col)

    _, created = await MedicationLogRepository.upsert_log(_sample_log(now))

    assert created is False


@pytest.mark.asyncio
async def test_upsert_log_treats_duplicate_key_as_existing(
    override_medication_logs_col,
):
    """
    唯一索引擋下併發插入時，代表另一個實例先建立了這筆 log。
    對本實例而言等同「已存在」，不得回報 created=True 而重複通知。
    """
    col = MagicMock()
    now = datetime.now(tz=timezone.utc)
    col.update_one = AsyncMock(side_effect=DuplicateKeyError("duplicate"))
    col.find_one = AsyncMock(return_value=_fake_log_doc(now))
    override_medication_logs_col(col)

    result, created = await MedicationLogRepository.upsert_log(_sample_log(now))

    assert result.id == "L123"
    assert created is False


@pytest.mark.asyncio
async def test_claim_patient_reminder_guards_on_flag(override_medication_logs_col):
    """
    搶佔必須把「旗標仍為 False」放進 filter，否則兩個實例會各推播一次。
    """
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    override_medication_logs_col(col)

    assert await MedicationLogRepository.claim_patient_reminder("L123") is True
    args, _ = col.update_one.await_args
    assert args[0] == {"_id": "L123", "patient_reminder_sent": False}
    assert args[1] == {"$set": {"patient_reminder_sent": True}}


@pytest.mark.asyncio
async def test_claim_patient_reminder_returns_false_when_lost(
    override_medication_logs_col,
):
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=0))
    override_medication_logs_col(col)

    assert await MedicationLogRepository.claim_patient_reminder("L123") is False


@pytest.mark.asyncio
async def test_release_caregiver_alert_does_not_clobber_taken(
    override_medication_logs_col,
):
    """
    還原家屬警報時，status 只能在仍是 missed 的情況下回寫 pending——
    使用者可能在推播失敗的空檔按下「已用藥」，那時 status 是 taken。
    """
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    override_medication_logs_col(col)

    await MedicationLogRepository.release_caregiver_alert("L123")
    args, _ = col.update_one.await_args
    assert args[0] == {
        "_id": "L123",
        "caregiver_alert_sent": True,
        "status": "missed",
    }


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
