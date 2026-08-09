from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from pymongo import ReturnDocument
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
async def test_find_or_create_reminder_upserts_atomically():
    """
    藥袋提交流程靠這個方法避免「查一次缺席才建立」的競態：兩個並行呼叫
    必須只有一個真的插入。這裡驗證的是查詢條件、upsert 旗標與只在建立時
    套用的欄位——原子性本身由 MongoDB 的單一 document 更新保證，不是
    這個測試能驗證的範圍。
    """
    from app.repositories.medication_repository import MedicationReminderRepository

    col = MagicMock()
    col.find_one_and_update = AsyncMock(
        return_value={
            "_id": "R_NEW",
            "creator_user_id": "U_FAMILY",
            "user_id": "U_PATIENT",
            "slot_type": "morning",
            "scheduled_time": "08:00",
            "start_date": "2026-08-10",
            "enabled": True,
            "medication_ids": [],
        }
    )

    reminder = await MedicationReminderRepository.find_or_create_reminder(
        user_id="U_PATIENT",
        slot_type="morning",
        creator_user_id="U_FAMILY",
        scheduled_time="08:00",
        collection=col,
    )

    assert reminder.id == "R_NEW"
    (query, update), kwargs = col.find_one_and_update.call_args
    assert query == {"user_id": "U_PATIENT", "slot_type": "morning"}
    set_on_insert = update["$setOnInsert"]
    assert set_on_insert["creator_user_id"] == "U_FAMILY"
    assert set_on_insert["scheduled_time"] == "08:00"
    assert set_on_insert["enabled"] is True
    assert set_on_insert["medication_ids"] == []
    assert kwargs.get("upsert") is True
    assert kwargs.get("return_document") is ReturnDocument.AFTER


@pytest.mark.asyncio
async def test_find_or_create_reminder_returns_existing_without_overwriting():
    """命中既有規則時只回傳它，$setOnInsert 不該影響既有文件的欄位——
    這一點由 MongoDB 語意保證，這裡驗證的是回傳值確實是資料庫回來的既有文件。
    """
    from app.repositories.medication_repository import MedicationReminderRepository

    col = MagicMock()
    col.find_one_and_update = AsyncMock(
        return_value={
            "_id": "R_EXISTING",
            "creator_user_id": "U_OTHER_CREATOR",
            "user_id": "U_PATIENT",
            "slot_type": "morning",
            "scheduled_time": "09:15",
            "enabled": False,
        }
    )

    reminder = await MedicationReminderRepository.find_or_create_reminder(
        user_id="U_PATIENT",
        slot_type="morning",
        creator_user_id="U_FAMILY",
        scheduled_time="08:00",
        collection=col,
    )

    assert reminder.id == "R_EXISTING"
    assert reminder.creator_user_id == "U_OTHER_CREATOR"
    assert reminder.scheduled_time == "09:15"
    assert reminder.enabled is False


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


# --- 藥品（medications）與提醒的關聯 -------------------------------------
# 新增的方法一律以 collection= 注入替身，不使用 monkeypatch。

def _medications_col() -> MagicMock:
    col = MagicMock()
    col.insert_many = AsyncMock()
    col.update_one = AsyncMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])
    col.find = MagicMock(return_value=cursor)
    return col


@pytest.mark.asyncio
async def test_create_many_assigns_ids_and_returns_medications():
    from app.models.medication import Medication
    from app.repositories.medication_repository import MedicationRepository

    col = _medications_col()
    medications = [
        Medication(user_id="U_P", created_by_user_id="U_F", name="藥一"),
        Medication(user_id="U_P", created_by_user_id="U_F", name="藥二"),
    ]

    created = await MedicationRepository.create_many(medications, collection=col)

    assert [medication.name for medication in created] == ["藥一", "藥二"]
    assigned_ids = [medication.id for medication in created]
    assert all(assigned_ids)
    assert len(set(assigned_ids)) == 2
    (documents,), _ = col.insert_many.call_args
    assert [document["_id"] for document in documents] == assigned_ids
    assert [document["name"] for document in documents] == ["藥一", "藥二"]
    assert all(document["_id"] for document in documents)


@pytest.mark.asyncio
async def test_create_many_with_empty_list_does_not_touch_the_database():
    from app.repositories.medication_repository import MedicationRepository

    col = _medications_col()

    created = await MedicationRepository.create_many([], collection=col)

    assert created == []
    col.insert_many.assert_not_called()


@pytest.mark.asyncio
async def test_find_by_ids_queries_by_id_set():
    from app.repositories.medication_repository import MedicationRepository

    col = _medications_col()

    await MedicationRepository.find_by_ids(["M1", "M2"], collection=col)

    (query,), _ = col.find.call_args
    assert query == {"_id": {"$in": ["M1", "M2"]}}


@pytest.mark.asyncio
async def test_find_by_ids_with_empty_list_does_not_query():
    from app.repositories.medication_repository import MedicationRepository

    col = _medications_col()

    found = await MedicationRepository.find_by_ids([], collection=col)

    assert found == []
    col.find.assert_not_called()


@pytest.mark.asyncio
async def test_find_active_by_ids_excludes_disabled_and_out_of_range():
    from app.repositories.medication_repository import MedicationRepository

    col = _medications_col()

    await MedicationRepository.find_active_by_ids(
        ["M1"], "2026-08-09", collection=col
    )

    (query,), _ = col.find.call_args
    assert query["_id"] == {"$in": ["M1"]}
    assert query["enabled"] is True
    conditions = query["$and"]
    assert {"$or": [{"start_date": {"$exists": False}}, {"start_date": {"$lte": "2026-08-09"}}]} in conditions
    assert {
        "$or": [
            {"end_date": None},
            {"end_date": {"$exists": False}},
            {"end_date": {"$gte": "2026-08-09"}},
        ]
    } in conditions


@pytest.mark.asyncio
async def test_set_enabled_updates_only_that_medication():
    from app.repositories.medication_repository import MedicationRepository

    col = _medications_col()
    col.update_one.return_value = MagicMock(matched_count=1)

    updated = await MedicationRepository.set_enabled(
        "M1", "U_P", False, collection=col
    )

    assert updated is True
    (query, update), _ = col.update_one.call_args
    assert query == {"_id": "M1", "user_id": "U_P"}
    assert update["$set"]["enabled"] is False


@pytest.mark.asyncio
async def test_link_medications_uses_add_to_set_to_avoid_duplicates():
    from app.repositories.medication_repository import MedicationReminderRepository

    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

    linked = await MedicationReminderRepository.link_medications_to_reminder(
        "R1", ["M1", "M2"], collection=col
    )

    assert linked is True
    (query, update), _ = col.update_one.call_args
    assert query == {"_id": "R1"}
    assert update["$addToSet"]["medication_ids"] == {"$each": ["M1", "M2"]}


@pytest.mark.asyncio
async def test_link_medications_with_empty_list_does_not_update():
    from app.repositories.medication_repository import MedicationReminderRepository

    col = MagicMock()
    col.update_one = AsyncMock()

    linked = await MedicationReminderRepository.link_medications_to_reminder(
        "R1", [], collection=col
    )

    assert linked is False
    col.update_one.assert_not_called()
