"""緊急回報稽核（emergency_reports）：只可追加、60 天 TTL、頻率計數。"""

import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import main
from app.models.safety import EmergencyReportEntry
from app.repositories.emergency_report_repository import EmergencyReportRepository

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _collection() -> MagicMock:
    collection = MagicMock()
    collection.create_index = AsyncMock()
    collection.insert_many = AsyncMock()
    collection.count_documents = AsyncMock(return_value=2)
    return collection


def _entry(**kwargs) -> EmergencyReportEntry:
    kwargs.setdefault("report_id", "r1")
    kwargs.setdefault("reporter_id", "U_GRANDSON")
    kwargs.setdefault("person_kind", "family")
    kwargs.setdefault("outcome", "sent")
    kwargs.setdefault("reported_at", NOW)
    kwargs.setdefault("expires_at", NOW + timedelta(days=60))
    return EmergencyReportEntry(**kwargs)


@pytest.mark.asyncio
async def test_ensure_indexes_creates_count_indexes_and_ttl():
    collection = _collection()

    await EmergencyReportRepository.ensure_indexes(collection=collection)

    keys = [call.args[0] for call in collection.create_index.call_args_list]
    assert [("reporter_id", 1), ("reported_at", -1)] in keys
    assert [("patient_id", 1), ("reported_at", -1)] in keys
    ttl = next(c for c in collection.create_index.call_args_list if c.args[0] == "expires_at")
    assert ttl.kwargs.get("expireAfterSeconds") == 0


def test_startup_creates_the_emergency_report_indexes():
    """寫了 ensure_indexes 卻沒人呼叫，正式環境就永遠沒有 TTL 與計數索引。"""
    source = inspect.getsource(main.lifespan)
    assert "EmergencyReportRepository.ensure_indexes" in source


def test_repository_is_append_only():
    public = {name for name in vars(EmergencyReportRepository) if not name.startswith("_")}
    assert public == {"ensure_indexes", "append_many", "count_cross_person_sent"}


@pytest.mark.asyncio
async def test_append_many_inserts_every_entry():
    collection = _collection()
    entries = [_entry(patient_id="U_GRANDPA"), _entry(person_kind="self", patient_id="U_GRANDSON")]

    await EmergencyReportRepository.append_many(entries, collection=collection)

    (documents,) = collection.insert_many.await_args.args
    assert [d["patient_id"] for d in documents] == ["U_GRANDPA", "U_GRANDSON"]
    assert all(d["expires_at"] == NOW + timedelta(days=60) for d in documents)


@pytest.mark.asyncio
async def test_append_many_with_nothing_skips_the_write():
    collection = _collection()
    await EmergencyReportRepository.append_many([], collection=collection)
    collection.insert_many.assert_not_awaited()


@pytest.mark.asyncio
async def test_count_only_includes_delivered_cross_person_reports():
    collection = _collection()
    since = NOW - timedelta(hours=1)

    count = await EmergencyReportRepository.count_cross_person_sent(
        patient_id="U_GRANDPA", since=since, collection=collection
    )

    assert count == 2
    (query,) = collection.count_documents.await_args.args
    assert query == {
        "cross_person": True,
        "outcome": "sent",
        "reported_at": {"$gte": since},
        "patient_id": "U_GRANDPA",
    }
