"""用藥提醒拉霸在資料層的三件事：寫入這一頓挑的選項、讀出可學習的紀錄、
長輩改了提醒設定時讓那一頓的催促時機退出學習。"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo import ReturnDocument

from app.repositories.medication_repository import MedicationLogRepository

NOW = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
URGENT = datetime(2026, 9, 14, 0, 10, tzinfo=timezone.utc)
TIMEOUT = datetime(2026, 9, 14, 0, 30, tzinfo=timezone.utc)


def _doc(**overrides):
    doc = {
        "_id": "L1",
        "reminder_id": "R1",
        "user_id": "U_PATIENT",
        "alert_notify_user_id": "U_CARE",
        "slot_type": "morning",
        "scheduled_at": NOW,
        "timeout_at": TIMEOUT,
        "status": "pending",
    }
    doc.update(overrides)
    return doc


def _col(*, updated=None, existing=None):
    col = MagicMock()
    col.find_one_and_update = AsyncMock(return_value=updated)
    col.find_one = AsyncMock(return_value=existing)
    return col


@pytest.mark.asyncio
async def test_assign_writes_once_and_only_if_the_schedule_has_not_changed():
    """先寫的為準（重送、多實例），而且逾時時間必須還是挑選時看到的那個：
    長輩剛好改了提醒設定時，不能用舊的最晚時刻蓋掉剛對齊好的催促時間。"""
    col = _col(updated=_doc(reminder_tone="family", nudge_minutes=10, urgent_at=URGENT))

    await MedicationLogRepository.assign_reminder_variant(
        "L1", tone="family", nudge_minutes=10, urgent_at=URGENT,
        expected_timeout_at=TIMEOUT, collection=col,
    )

    (query, update), kwargs = col.find_one_and_update.await_args
    assert query == {"_id": "L1", "reminder_tone": None, "timeout_at": TIMEOUT}
    assert update == {
        "$set": {"reminder_tone": "family", "nudge_minutes": 10, "urgent_at": URGENT}
    }
    assert kwargs["return_document"] == ReturnDocument.AFTER


@pytest.mark.asyncio
async def test_assign_returns_the_document_it_wrote_without_a_second_read():
    """寫入與讀回是同一次操作：不會出現「寫進去了、卻讀不回來而照現行版本送」。"""
    col = _col(updated=_doc(reminder_tone="brief", nudge_minutes=15, urgent_at=URGENT))

    log = await MedicationLogRepository.assign_reminder_variant(
        "L1", tone="brief", nudge_minutes=15, urgent_at=URGENT,
        expected_timeout_at=TIMEOUT, collection=col,
    )

    assert (log.reminder_tone, log.nudge_minutes) == ("brief", 15)
    col.find_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_assign_reads_back_what_the_database_holds_when_it_did_not_write():
    """另一個實例先寫了 family，或逾時時間已經被改掉：照資料庫裡那一份。"""
    col = _col(updated=None, existing=_doc(reminder_tone="family", nudge_minutes=20))

    log = await MedicationLogRepository.assign_reminder_variant(
        "L1", tone="brief", nudge_minutes=10, urgent_at=URGENT,
        expected_timeout_at=TIMEOUT, collection=col,
    )

    assert (log.reminder_tone, log.nudge_minutes) == ("family", 20)


@pytest.mark.asyncio
async def test_assign_returns_none_when_the_log_is_gone():
    col = _col(updated=None, existing=None)

    log = await MedicationLogRepository.assign_reminder_variant(
        "L1", tone="brief", nudge_minutes=20, urgent_at=URGENT,
        expected_timeout_at=TIMEOUT, collection=col,
    )

    assert log is None


@pytest.mark.asyncio
async def test_assign_leaves_the_nudge_time_alone_when_timing_is_not_applied():
    """T+0 晚送的那一頓只進語氣拉霸：催促時間維持展開時的 +20，時機記 None。"""
    col = _col(updated=_doc(reminder_tone="brief"))

    await MedicationLogRepository.assign_reminder_variant(
        "L1", tone="brief", nudge_minutes=None, urgent_at=None,
        expected_timeout_at=TIMEOUT, collection=col,
    )

    (_query, update), _ = col.find_one_and_update.await_args
    assert update == {"$set": {"reminder_tone": "brief", "nudge_minutes": None}}


@pytest.mark.asyncio
async def test_list_variant_outcomes_reads_only_delivered_bandit_logs_that_have_a_result():
    """還沒走完與被取消的沒有結果；上線前的紀錄沒有選項；T+0 重試用完、根本沒送達
    的那一頓也不算（長輩沒看到訊息，與挑了哪一版無關）。"""
    col = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(
        return_value=[_doc(status="missed", reminder_tone="brief", nudge_minutes=15)]
    )
    col.find = MagicMock(return_value=cursor)

    logs = await MedicationLogRepository.list_variant_outcomes(collection=col)

    (query,), _ = col.find.call_args
    assert query == {
        "reminder_tone": {"$ne": None},
        "status": {"$in": ["taken", "missed"]},
        "patient_reminder_attempts": {"$not": {"$gte": 5}},
    }
    assert [(log.id, log.reminder_tone, log.nudge_minutes) for log in logs] == [("L1", "brief", 15)]


@pytest.mark.asyncio
async def test_resync_drops_the_bandit_nudge_when_it_rewrites_urgent_at():
    """改了提醒設定，催促時間被重設回 +20：那一頓的時機已經不是拉霸挑的，
    不能再拿來學。語氣照舊有效（卡片已經照那個語氣送出去了），所以不動。"""
    col = MagicMock()
    col.update_many = AsyncMock(
        side_effect=[MagicMock(modified_count=0), MagicMock(modified_count=1)]
    )

    await MedicationLogRepository.resync_pending_by_reminder(
        "R1",
        scheduled_at=NOW,
        slot_type="morning",
        urgent_at=URGENT,
        timeout_at=TIMEOUT,
        collection=col,
    )

    (_query, retag_update), _ = col.update_many.call_args_list[1]
    assert retag_update["$set"]["nudge_minutes"] is None
    assert "reminder_tone" not in retag_update["$set"]
