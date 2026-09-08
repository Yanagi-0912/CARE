"""角色與委任變更的稽核紀錄（append-only）。

這裡最重要的一條測試是「沒有更新與刪除的入口」——一份可以被改寫的稽核紀錄，
回答不了「誰在什麼時候給了誰權限」這個問題，而那正是它存在的唯一理由。
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.family_role_audit_repository import FamilyRoleAuditRepository

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
OWNER = "U-owner"
MEMBER = "U-member"


def make_collection(docs=None):
    collection = MagicMock()
    collection.insert_one = AsyncMock()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=docs or [])
    collection.find.return_value = cursor
    collection.create_index = AsyncMock()
    return collection


@pytest.mark.asyncio
async def test_append_records_role_change_with_before_and_after():
    collection = make_collection()
    entry = await FamilyRoleAuditRepository.append(
        owner_id=OWNER,
        member_id=MEMBER,
        changed_by=OWNER,
        from_role="MEMBER",
        to_role="GUARDIAN",
        now=NOW,
        collection=collection,
    )
    assert entry.from_role == "MEMBER"
    assert entry.to_role == "GUARDIAN"
    assert entry.changed_at == NOW
    assert entry.event == "role_change"
    written = collection.insert_one.await_args.args[0]
    assert written["owner_id"] == OWNER
    assert written["changed_by"] == OWNER


@pytest.mark.asyncio
async def test_via_delegation_distinguishes_who_actually_decided():
    """事後要分得出「長輩自己指派的」與「別人代他指派的」。

    兩者性質完全不同：後者是在擁有者無法表達意願時發生的授權。
    """
    collection = make_collection()
    direct = await FamilyRoleAuditRepository.append(
        owner_id=OWNER, member_id=MEMBER, changed_by=OWNER,
        to_role="CAREGIVER", now=NOW, collection=collection,
    )
    delegated = await FamilyRoleAuditRepository.append(
        owner_id=OWNER, member_id=MEMBER, changed_by="U-delegate",
        to_role="CAREGIVER", via_delegation=True, now=NOW, collection=collection,
    )
    assert direct.via_delegation is False
    assert delegated.via_delegation is True


@pytest.mark.asyncio
async def test_delegation_events_share_the_same_audit_trail():
    """委任的建立與撤銷走同一份稽核，否則事件時序拼不回來。"""
    collection = make_collection()
    entry = await FamilyRoleAuditRepository.append(
        owner_id=OWNER,
        member_id="U-delegate",
        changed_by="U-approver",
        event="delegation_granted",
        now=NOW,
        collection=collection,
    )
    assert entry.event == "delegation_granted"


@pytest.mark.asyncio
async def test_repository_offers_no_update_or_delete():
    names = [n for n in dir(FamilyRoleAuditRepository) if not n.startswith("_")]
    assert not any(
        keyword in name
        for name in names
        for keyword in ("update", "delete", "remove", "replace")
    ), f"稽核紀錄不得提供修改或刪除的介面，但找到：{names}"


@pytest.mark.asyncio
async def test_list_for_owner_sorts_newest_first():
    collection = make_collection()
    await FamilyRoleAuditRepository.list_for_owner(OWNER, collection=collection)
    collection.find.return_value.sort.assert_called_once_with("changed_at", -1)
