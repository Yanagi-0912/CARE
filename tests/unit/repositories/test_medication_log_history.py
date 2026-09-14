"""依人、依時間區間查用藥歷史（查服藥狀況用）。"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.medication import MedicationLog
from app.repositories.medication_repository import MedicationLogRepository

START = datetime(2026, 9, 7, 16, tzinfo=timezone.utc)
END = datetime(2026, 9, 14, 16, tzinfo=timezone.utc)


@pytest.fixture()
def logs_col(monkeypatch):
    col = MagicMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=[])
    col.find = MagicMock(return_value=cursor)
    col.create_index = AsyncMock()
    monkeypatch.setattr(
        "app.repositories.medication_repository.MongoDBManager.get_medication_logs_collection",
        lambda: col,
    )
    return col


async def test_history_between_filters_by_person_and_time_and_skips_cancelled(logs_col):
    """cancelled 是排程器的內部記帳、不是使用者做過的事，歷史一律不列（見 spec）。"""
    await MedicationLogRepository.list_logs_by_user_between("U1", START, END)

    (query,), _ = logs_col.find.call_args
    assert query == {
        "user_id": "U1",
        "status": {"$ne": "cancelled"},
        "scheduled_at": {"$gte": START, "$lt": END},
    }
    logs_col.find.return_value.sort.assert_called_once_with("scheduled_at", 1)


async def test_history_between_returns_parsed_logs(logs_col):
    logs_col.find.return_value.to_list = AsyncMock(
        return_value=[
            {
                "_id": "abc",
                "reminder_id": "r1",
                "user_id": "U1",
                "alert_notify_user_id": "U2",
                "slot_type": "morning",
                "scheduled_at": datetime(2026, 9, 13, 23, 30),
                "timeout_at": datetime(2026, 9, 14, 0, 0),
                "status": "taken",
            }
        ]
    )

    logs = await MedicationLogRepository.list_logs_by_user_between("U1", START, END)

    assert [type(log) for log in logs] == [MedicationLog]
    assert logs[0].id == "abc"


async def test_ensure_indexes_adds_a_per_person_time_index(logs_col):
    """依人查詢沒有索引就會掃整張表；既有的唯一索引照舊建立。"""
    await MedicationLogRepository.ensure_indexes()

    keys = [call.args[0] for call in logs_col.create_index.call_args_list]
    assert [("user_id", 1), ("scheduled_at", 1)] in keys
    assert [("reminder_id", 1), ("scheduled_at", 1)] in keys
