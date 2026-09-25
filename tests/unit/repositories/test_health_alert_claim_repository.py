from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.errors import DuplicateKeyError

from app.repositories.health_alert_claim_repository import HealthAlertClaimRepository


def _collection() -> MagicMock:
    collection = MagicMock()
    collection.create_index = AsyncMock()
    collection.insert_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=None)
    return collection


@pytest.mark.asyncio
async def test_ensure_indexes_creates_unique_user_alert_key_and_ttl():
    collection = _collection()

    await HealthAlertClaimRepository.ensure_indexes(collection=collection)

    unique_call = next(
        call
        for call in collection.create_index.call_args_list
        if call.args[0] == [("user_id", 1), ("alert_key", 1)]
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

    granted = await HealthAlertClaimRepository.try_claim(
        user_id="U1", alert_key="bp_high", ttl_minutes=30, collection=collection
    )

    assert granted is True
    collection.insert_one.assert_awaited_once()
    document = collection.insert_one.await_args.args[0]
    assert document["user_id"] == "U1"
    assert document["alert_key"] == "bp_high"


@pytest.mark.asyncio
async def test_try_claim_never_reads_before_writing():
    """通報權必須由唯一索引原子取得（同 safety_alert_repository 的模式）：
    讀後寫會讓同一位使用者連送兩則相似訊息時，兩邊都判斷未通報而各推一次。
    """
    collection = _collection()

    await HealthAlertClaimRepository.try_claim(
        user_id="U1", alert_key="bp_high", ttl_minutes=30, collection=collection
    )

    collection.find_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_try_claim_expires_after_the_requested_window():
    collection = _collection()

    await HealthAlertClaimRepository.try_claim(
        user_id="U1", alert_key="glucose_low", ttl_minutes=30, collection=collection
    )

    document = collection.insert_one.await_args.args[0]
    window = document["expires_at"] - document["claimed_at"]
    assert window.total_seconds() == pytest.approx(30 * 60)


@pytest.mark.asyncio
async def test_try_claim_returns_false_on_second_attempt_within_the_window():
    """節流期間內第二次取得 SHALL 失敗（health-alerts spec「重複推播的
    節流」）。DuplicateKeyError 代表節流期間內已通報過，不是錯誤。"""
    collection = _collection()
    collection.insert_one = AsyncMock(side_effect=DuplicateKeyError("duplicate"))

    granted = await HealthAlertClaimRepository.try_claim(
        user_id="U1", alert_key="bp_high", ttl_minutes=30, collection=collection
    )

    assert granted is False


@pytest.mark.asyncio
async def test_release_deletes_only_that_claim():
    """緊急通報沒有送到任何人時交還名額，下一次才不會被當成重複擋下。"""
    collection = _collection()
    collection.delete_one = AsyncMock()

    await HealthAlertClaimRepository.release(
        user_id="U_GRANDPA", alert_key="emergency_detected", collection=collection
    )

    collection.delete_one.assert_awaited_once_with(
        {"user_id": "U_GRANDPA", "alert_key": "emergency_detected"}
    )
