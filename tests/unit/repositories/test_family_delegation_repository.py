"""委任紀錄的建立、有效性與撤銷。

重點在三件事：預設 90 天效期、失效不刪除文件、以及「有效」的定義在查詢條件
裡就成立——服務層拿到什麼就是什麼，不必自己再判一次過期，那會多一個判錯的
地方。

collection 以參數注入（沿用 ConsultationRepository 等既有 repository 的慣例），
`now` 也可注入，測試不必等真實時間流逝就能構造到期情境。
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.family_tree import DELEGATION_DEFAULT_VALID_DAYS, FamilyDelegation
from app.repositories.family_delegation_repository import FamilyDelegationRepository

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
OWNER = "U-owner"
DELEGATE = "U-delegate"


def make_collection(find_one_result=None, find_results=None):
    collection = MagicMock()
    collection.insert_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=find_one_result)
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=find_results or [])
    collection.find.return_value = cursor
    collection.update_many = AsyncMock(
        return_value=MagicMock(modified_count=1)
    )
    collection.create_index = AsyncMock()
    return collection


@pytest.mark.asyncio
async def test_grant_defaults_to_ninety_days():
    collection = make_collection()
    delegation = await FamilyDelegationRepository.grant(
        owner_id=OWNER,
        delegate_user_id=DELEGATE,
        granted_by="U-approver",
        now=NOW,
        collection=collection,
    )
    assert delegation.expires_at == NOW + timedelta(days=DELEGATION_DEFAULT_VALID_DAYS)
    assert DELEGATION_DEFAULT_VALID_DAYS == 90


@pytest.mark.asyncio
async def test_grant_records_provenance_fields():
    """稽核要回答「誰在什麼時候依據什麼取得了這項委任」。"""
    collection = make_collection()
    delegation = await FamilyDelegationRepository.grant(
        owner_id=OWNER,
        delegate_user_id=DELEGATE,
        granted_by="U-approver",
        approval_ref="approval-doc-1",
        now=NOW,
        collection=collection,
    )
    assert delegation.granted_at == NOW
    assert delegation.granted_by == "U-approver"
    assert delegation.approval_ref == "approval-doc-1"
    assert delegation.revoked_at is None
    written = collection.insert_one.await_args.args[0]
    for field in ("owner_id", "delegate_user_id", "granted_at", "granted_by", "expires_at"):
        assert field in written


@pytest.mark.asyncio
async def test_grant_rejects_non_positive_validity():
    """委任 SHALL NOT 永久存在，也不得零效期。"""
    collection = make_collection()
    with pytest.raises(ValueError):
        await FamilyDelegationRepository.grant(
            owner_id=OWNER,
            delegate_user_id=DELEGATE,
            granted_by="U-approver",
            valid_days=0,
            collection=collection,
        )


@pytest.mark.asyncio
async def test_has_active_delegation_filters_revoked_and_expired_in_the_query():
    """有效性在查詢條件裡就成立，不是撈回來再過濾。

    這一點很重要：若查詢不帶條件、由呼叫端自行判斷，任何一個忘了判斷的
    呼叫端就會放行一筆已撤銷的委任。
    """
    collection = make_collection(find_one_result={"_id": "x"})
    await FamilyDelegationRepository.has_active_delegation(
        owner_id=OWNER, delegate_user_id=DELEGATE, now=NOW, collection=collection
    )
    query = collection.find_one.await_args.args[0]
    assert query["owner_id"] == OWNER
    assert query["delegate_user_id"] == DELEGATE
    assert query["revoked_at"] is None
    assert query["expires_at"] == {"$gt": NOW}


@pytest.mark.asyncio
async def test_has_active_delegation_false_when_nothing_matches():
    collection = make_collection(find_one_result=None)
    assert (
        await FamilyDelegationRepository.has_active_delegation(
            owner_id=OWNER, delegate_user_id=DELEGATE, now=NOW, collection=collection
        )
        is False
    )


@pytest.mark.asyncio
async def test_list_active_excludes_revoked_and_expired():
    collection = make_collection(
        find_results=[
            {
                "owner_id": OWNER,
                "delegate_user_id": DELEGATE,
                "granted_at": NOW,
                "granted_by": "U-approver",
                "expires_at": NOW + timedelta(days=90),
            }
        ]
    )
    active = await FamilyDelegationRepository.list_active(
        OWNER, now=NOW, collection=collection
    )
    assert len(active) == 1
    query = collection.find.call_args.args[0]
    assert query["revoked_at"] is None
    assert query["expires_at"] == {"$gt": NOW}


@pytest.mark.asyncio
async def test_revoke_marks_instead_of_deleting():
    """撤銷是標記而非刪除——委任存續的那段期間正是最需要事後查得到的一段。"""
    collection = make_collection()
    await FamilyDelegationRepository.revoke(
        owner_id=OWNER, delegate_user_id=DELEGATE, revoked_by=OWNER,
        now=NOW, collection=collection,
    )
    assert not hasattr(collection, "delete_one") or not collection.delete_one.called
    query, update = collection.update_many.await_args.args
    assert update["$set"]["revoked_at"] == NOW
    assert update["$set"]["revoked_by"] == OWNER
    # 已撤銷者不重複標記，否則第二次撤銷會蓋掉原本的時間戳
    assert query["revoked_at"] is None


@pytest.mark.asyncio
async def test_repository_has_no_delete_method():
    """稽核與 provenance 需要紀錄一直在，因此連刪除的入口都不提供。"""
    method_names = dir(FamilyDelegationRepository)
    assert not any("delete" in name for name in method_names)


@pytest.mark.asyncio
async def test_list_all_for_audit_does_not_filter():
    """稽核查詢要看得到全部，含已到期與已撤銷者。"""
    collection = make_collection(find_results=[])
    await FamilyDelegationRepository.list_all_for_audit(OWNER, collection=collection)
    query = collection.find.call_args.args[0]
    assert query == {"owner_id": OWNER}


@pytest.mark.asyncio
async def test_ensure_indexes_does_not_create_a_ttl_index():
    """到期的委任要留著供稽核，不能讓資料庫自動清掉。

    這與 safety_alerts 的節流紀錄相反——那裡的紀錄過期就沒有保留價值。
    """
    collection = make_collection()
    await FamilyDelegationRepository.ensure_indexes(collection=collection)
    for call in collection.create_index.await_args_list:
        assert "expireAfterSeconds" not in call.kwargs


def test_is_active_at_handles_naive_datetimes_from_mongo():
    """pymongo 以 naive UTC 讀回 datetime，直接與帶時區的 now 比較會拋 TypeError。"""
    delegation = FamilyDelegation(
        owner_id=OWNER,
        delegate_user_id=DELEGATE,
        granted_at=NOW,
        granted_by="U-approver",
        expires_at=datetime(2026, 11, 22, 12, 0),  # naive，模擬資料庫讀回
    )
    assert delegation.is_active_at(NOW) is True
    assert delegation.is_active_at(datetime(2026, 12, 1, tzinfo=timezone.utc)) is False


def test_revoked_delegation_is_never_active():
    delegation = FamilyDelegation(
        owner_id=OWNER,
        delegate_user_id=DELEGATE,
        granted_at=NOW,
        granted_by="U-approver",
        expires_at=NOW + timedelta(days=90),
        revoked_at=NOW,
    )
    assert delegation.is_active_at(NOW) is False
