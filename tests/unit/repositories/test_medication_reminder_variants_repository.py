"""用藥提醒拉霸在資料層的三件事：寫入這一頓挑的選項、讀出可學習的紀錄、
長輩改了提醒設定時讓那一頓的催促時機退出學習。"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.medication_repository import MedicationLogRepository

NOW = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)
URGENT = datetime(2026, 9, 14, 0, 10, tzinfo=timezone.utc)


def _doc(**overrides):
    doc = {
        "_id": "L1",
        "reminder_id": "R1",
        "user_id": "U_PATIENT",
        "alert_notify_user_id": "U_CARE",
        "slot_type": "morning",
        "scheduled_at": NOW,
        "timeout_at": NOW,
        "status": "pending",
    }
    doc.update(overrides)
    return doc


@pytest.mark.asyncio
async def test_assign_reminder_variant_writes_only_when_not_yet_assigned():
    """推播失敗重試、或多個實例同時送 T+0 時，後到的不能蓋掉先到的選擇。"""
    col = MagicMock()
    col.update_one = AsyncMock()
    col.find_one = AsyncMock(
        return_value=_doc(reminder_tone="family", nudge_minutes=10, urgent_at=URGENT)
    )

    await MedicationLogRepository.assign_reminder_variant(
        "L1", tone="family", nudge_minutes=10, urgent_at=URGENT, collection=col
    )

    (query, update), _ = col.update_one.await_args
    assert query == {"_id": "L1", "reminder_tone": None}
    assert update == {
        "$set": {"reminder_tone": "family", "nudge_minutes": 10, "urgent_at": URGENT}
    }


@pytest.mark.asyncio
async def test_assign_reminder_variant_returns_what_the_database_holds():
    """另一個實例先寫了 brief：這次要的是 family，但卡片要照資料庫裡那一份送。"""
    col = MagicMock()
    col.update_one = AsyncMock()
    col.find_one = AsyncMock(
        return_value=_doc(reminder_tone="brief", nudge_minutes=15, urgent_at=URGENT)
    )

    log = await MedicationLogRepository.assign_reminder_variant(
        "L1", tone="family", nudge_minutes=10, urgent_at=URGENT, collection=col
    )

    assert (log.reminder_tone, log.nudge_minutes) == ("brief", 15)


@pytest.mark.asyncio
async def test_assign_reminder_variant_returns_none_when_the_log_is_gone():
    col = MagicMock()
    col.update_one = AsyncMock()
    col.find_one = AsyncMock(return_value=None)

    log = await MedicationLogRepository.assign_reminder_variant(
        "L1", tone="brief", nudge_minutes=20, urgent_at=URGENT, collection=col
    )

    assert log is None


@pytest.mark.asyncio
async def test_list_variant_outcomes_reads_only_bandit_logs_that_have_a_result():
    """還沒走完（pending）與被取消（cancelled）的沒有結果；上線前的紀錄沒有選項。"""
    col = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(
        return_value=[_doc(status="missed", reminder_tone="brief", nudge_minutes=15)]
    )
    col.find = MagicMock(return_value=cursor)

    logs = await MedicationLogRepository.list_variant_outcomes(collection=col)

    (query,), _ = col.find.call_args
    assert query == {"reminder_tone": {"$ne": None}, "status": {"$in": ["taken", "missed"]}}
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
        timeout_at=NOW,
        collection=col,
    )

    (_query, retag_update), _ = col.update_many.call_args_list[1]
    assert retag_update["$set"]["nudge_minutes"] is None
    assert "reminder_tone" not in retag_update["$set"]
