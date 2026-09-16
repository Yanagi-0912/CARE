from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.health import HealthAlertThreshold
from app.repositories.health_alert_threshold_repository import (
    HealthAlertThresholdRepository,
)


def _collection() -> MagicMock:
    collection = MagicMock()
    collection.create_index = AsyncMock()
    collection.update_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=None)
    return collection


@pytest.mark.asyncio
async def test_ensure_indexes_creates_unique_user_id_index():
    collection = _collection()

    await HealthAlertThresholdRepository.ensure_indexes(collection=collection)

    (index_spec,), kwargs = collection.create_index.call_args
    assert index_spec == "user_id"
    assert kwargs.get("unique") is True


@pytest.mark.asyncio
async def test_get_returns_none_when_no_document():
    """沒有文件等同全部未設定（design.md）。"""
    collection = _collection()

    result = await HealthAlertThresholdRepository.get("U1", collection=collection)

    assert result is None


@pytest.mark.asyncio
async def test_get_returns_threshold_when_found():
    collection = _collection()
    collection.find_one = AsyncMock(
        return_value={
            "user_id": "U1",
            "systolic_high": 140,
            "systolic_low": 100,
            "diastolic_high": None,
            "diastolic_low": None,
            "glucose_fasting_high": None,
            "glucose_nonfasting_high": None,
            "glucose_low": None,
            "updated_by": "U1",
            "updated_at": datetime.now(timezone.utc),
        }
    )

    result = await HealthAlertThresholdRepository.get("U1", collection=collection)

    assert result is not None
    assert result.systolic_high == 140


@pytest.mark.asyncio
async def test_upsert_writes_by_user_id_with_upsert_true():
    collection = _collection()
    collection.find_one = AsyncMock(
        return_value={
            "user_id": "U1",
            "systolic_high": 140,
            "systolic_low": 100,
            "updated_by": "U_GUARDIAN",
            "updated_at": datetime.now(timezone.utc),
        }
    )
    threshold = HealthAlertThreshold(
        user_id="U1", systolic_high=140, systolic_low=100, updated_by="U_GUARDIAN"
    )

    result = await HealthAlertThresholdRepository.upsert(threshold, collection=collection)

    assert result.systolic_high == 140
    collection.update_one.assert_awaited_once()
    (query, update), kwargs = collection.update_one.call_args
    assert query == {"user_id": "U1"}
    assert kwargs.get("upsert") is True
    assert update["$set"]["systolic_high"] == 140
    assert update["$set"]["updated_by"] == "U_GUARDIAN"
    # user_id 是唯一鍵，不該又出現在 $set 裡重複寫入。
    assert "user_id" not in update["$set"]


@pytest.mark.asyncio
async def test_upsert_can_clear_a_previously_set_field():
    """清除一項（health-alerts spec「清除一項」）：把已設定的欄位改為 None，
    $set 必須真的把它寫成 null，而不是被 exclude_none 濾掉留在原值。"""
    collection = _collection()
    collection.find_one = AsyncMock(
        return_value={
            "user_id": "U1",
            "diastolic_high": None,
            "updated_by": "U1",
            "updated_at": datetime.now(timezone.utc),
        }
    )
    threshold = HealthAlertThreshold(
        user_id="U1", diastolic_high=None, updated_by="U1"
    )

    await HealthAlertThresholdRepository.upsert(threshold, collection=collection)

    (_, update), _ = collection.update_one.call_args
    assert "diastolic_high" in update["$set"]
    assert update["$set"]["diastolic_high"] is None
