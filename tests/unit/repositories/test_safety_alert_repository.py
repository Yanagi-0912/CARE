from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.errors import DuplicateKeyError

from app.repositories.safety_alert_repository import SafetyAlertRepository


def _collection() -> MagicMock:
    collection = MagicMock()
    collection.create_index = AsyncMock()
    collection.insert_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=None)
    return collection


@pytest.mark.asyncio
async def test_ensure_indexes_creates_unique_user_drug_key_and_ttl():
    collection = _collection()

    await SafetyAlertRepository.ensure_indexes(collection=collection)

    unique_call = next(
        call
        for call in collection.create_index.call_args_list
        if call.args[0] == [("user_id", 1), ("drug_key", 1)]
    )
    assert unique_call.kwargs.get("unique") is True

    ttl_call = next(
        call
        for call in collection.create_index.call_args_list
        if call.args[0] == "expires_at"
    )
    assert ttl_call.kwargs.get("expireAfterSeconds") == 0


@pytest.mark.asyncio
async def test_try_claim_inserts_and_grants_the_notification_right():
    collection = _collection()

    granted = await SafetyAlertRepository.try_claim(
        user_id="U1",
        drug_key="合利他命EXPLUS",
        risk_level="high",
        ttl_hours=24,
        collection=collection,
    )

    assert granted is True
    collection.insert_one.assert_awaited_once()
    document = collection.insert_one.await_args.args[0]
    assert document["user_id"] == "U1"
    assert document["drug_key"] == "合利他命EXPLUS"
    assert document["risk_level"] == "high"


@pytest.mark.asyncio
async def test_try_claim_never_reads_before_writing():
    """讀後寫在同一位使用者連送兩則相似訊息時，兩邊都會判斷未通報而各推一次。

    通報權必須由唯一索引原子取得，因此這裡不得出現任何查詢。
    """
    collection = _collection()

    await SafetyAlertRepository.try_claim(
        user_id="U1",
        drug_key="某藥",
        risk_level="high",
        ttl_hours=24,
        collection=collection,
    )

    collection.find_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_try_claim_expires_after_the_requested_window():
    collection = _collection()

    await SafetyAlertRepository.try_claim(
        user_id="U1",
        drug_key="某藥",
        risk_level="high",
        ttl_hours=24,
        collection=collection,
    )

    document = collection.insert_one.await_args.args[0]
    window = document["expires_at"] - document["notified_at"]
    assert window.total_seconds() == pytest.approx(24 * 3600)


@pytest.mark.asyncio
async def test_try_claim_returns_false_when_already_notified():
    """DuplicateKeyError 就是「節流期間內已通報過」，不是錯誤。"""
    collection = _collection()
    collection.insert_one = AsyncMock(side_effect=DuplicateKeyError("duplicate"))

    granted = await SafetyAlertRepository.try_claim(
        user_id="U1",
        drug_key="某藥",
        risk_level="high",
        ttl_hours=24,
        collection=collection,
    )

    assert granted is False
