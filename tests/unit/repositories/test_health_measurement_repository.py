from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.health import HealthMeasurement
from app.repositories.health_measurement_repository import (
    HealthMeasurementRepository,
)


def _collection() -> MagicMock:
    collection = MagicMock()
    collection.create_index = AsyncMock()
    collection.insert_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=None)
    collection.delete_one = AsyncMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=[])
    collection.find = MagicMock(return_value=cursor)
    return collection


@pytest.mark.asyncio
async def test_ensure_indexes_creates_user_kind_measured_at_index():
    collection = _collection()

    await HealthMeasurementRepository.ensure_indexes(collection=collection)

    (index_spec,), kwargs = collection.create_index.call_args
    assert index_spec == [("user_id", 1), ("kind", 1), ("measured_at", -1)]


@pytest.mark.asyncio
async def test_add_assigns_id_and_returns_measurement():
    collection = _collection()
    measurement = HealthMeasurement(
        user_id="U_PATIENT",
        kind="blood_pressure",
        measured_at=datetime.now(timezone.utc),
        recorded_by="U_PATIENT",
        systolic=128,
        diastolic=82,
        level="within_range",
    )

    created = await HealthMeasurementRepository.add(measurement, collection=collection)

    assert created.id
    assert created.user_id == "U_PATIENT"
    collection.insert_one.assert_awaited_once()
    (document,), _ = collection.insert_one.call_args
    assert document["_id"] == created.id
    assert document["user_id"] == "U_PATIENT"
    # pulse 沒有提供，不該以 null 寫入資料庫。
    assert "pulse" not in document


@pytest.mark.asyncio
async def test_add_keeps_a_pre_assigned_id():
    collection = _collection()
    measurement = HealthMeasurement(
        id="M_FIXED",
        user_id="U1",
        kind="blood_glucose",
        measured_at=datetime.now(timezone.utc),
        recorded_by="U1",
        glucose_mg_dl=100,
        meal_context="fasting",
        level="within_range",
    )

    created = await HealthMeasurementRepository.add(measurement, collection=collection)

    assert created.id == "M_FIXED"
    (document,), _ = collection.insert_one.call_args
    assert document["_id"] == "M_FIXED"


@pytest.mark.asyncio
async def test_list_by_user_filters_by_kind_and_time_range_and_sorts_newest_first():
    collection = _collection()
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 31, tzinfo=timezone.utc)

    await HealthMeasurementRepository.list_by_user(
        "U1", kind="blood_pressure", start=start, end=end, collection=collection
    )

    (query,), _ = collection.find.call_args
    assert query == {
        "user_id": "U1",
        "kind": "blood_pressure",
        "measured_at": {"$gte": start, "$lte": end},
    }
    cursor = collection.find.return_value
    cursor.sort.assert_called_once_with("measured_at", -1)
    cursor.limit.assert_called_once_with(200)


@pytest.mark.asyncio
async def test_list_by_user_caps_at_200_by_default():
    collection = _collection()

    await HealthMeasurementRepository.list_by_user("U1", collection=collection)

    cursor = collection.find.return_value
    cursor.limit.assert_called_once_with(200)
    (query,), _ = collection.find.call_args
    assert query == {"user_id": "U1"}


@pytest.mark.asyncio
async def test_list_by_user_returns_parsed_measurements():
    collection = _collection()
    now = datetime.now(timezone.utc)
    collection.find.return_value.to_list = AsyncMock(
        return_value=[
            {
                "_id": "M1",
                "user_id": "U1",
                "kind": "blood_pressure",
                "measured_at": now,
                "recorded_by": "U1",
                "systolic": 120,
                "diastolic": 80,
                "level": "within_range",
            }
        ]
    )

    results = await HealthMeasurementRepository.list_by_user("U1", collection=collection)

    assert len(results) == 1
    assert results[0].id == "M1"


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_missing():
    collection = _collection()

    result = await HealthMeasurementRepository.get_by_id("MISSING", collection=collection)

    assert result is None


@pytest.mark.asyncio
async def test_get_by_id_returns_measurement_when_found():
    collection = _collection()
    now = datetime.now(timezone.utc)
    collection.find_one = AsyncMock(
        return_value={
            "_id": "M1",
            "user_id": "U1",
            "kind": "blood_pressure",
            "measured_at": now,
            "recorded_by": "U1",
            "systolic": 120,
            "diastolic": 80,
            "level": "within_range",
        }
    )

    result = await HealthMeasurementRepository.get_by_id("M1", collection=collection)

    assert result is not None
    assert result.id == "M1"


@pytest.mark.asyncio
async def test_delete_returns_true_when_a_document_was_removed():
    collection = _collection()
    collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))

    deleted = await HealthMeasurementRepository.delete("M1", collection=collection)

    assert deleted is True
    collection.delete_one.assert_awaited_once_with({"_id": "M1"})


@pytest.mark.asyncio
async def test_delete_returns_false_when_nothing_matched():
    collection = _collection()
    collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=0))

    deleted = await HealthMeasurementRepository.delete("MISSING", collection=collection)

    assert deleted is False
