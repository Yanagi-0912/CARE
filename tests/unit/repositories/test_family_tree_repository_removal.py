"""FamilyTreeRepository.remove_member：拿掉一位成員，並交回他原本的角色供稽核。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.family_tree_repository import FamilyTreeRepository

OWNER = "U-owner"
MEMBER = "U-member"


def make_collection(before):
    collection = MagicMock()
    collection.find_one_and_update = AsyncMock(return_value=before)
    return collection


@pytest.mark.asyncio
async def test_pulls_the_member_and_returns_their_previous_role():
    collection = make_collection(
        {"user_id": OWNER, "family_members": [{"user_id": MEMBER, "family_role": "GUARDIAN"}]}
    )

    removed = await FamilyTreeRepository.remove_member(OWNER, MEMBER, collection=collection)

    assert removed.user_id == MEMBER
    assert removed.family_role == "GUARDIAN"
    query, update = collection.find_one_and_update.await_args.args
    assert query == {"user_id": OWNER, "family_members.user_id": MEMBER}
    assert update["$pull"] == {"family_members": {"user_id": MEMBER}}


@pytest.mark.asyncio
async def test_does_not_touch_the_migration_state():
    """移除成員 SHALL NOT 成為讓家庭退回 legacy 授權的路徑。"""
    collection = make_collection(
        {"user_id": OWNER, "family_members": [{"user_id": MEMBER}]}
    )

    await FamilyTreeRepository.remove_member(OWNER, MEMBER, collection=collection)

    _, update = collection.find_one_and_update.await_args.args
    assert "rbac_migration_state" not in update.get("$set", {})
    assert "rbac_migration_state" not in update.get("$unset", {})


@pytest.mark.asyncio
async def test_not_in_the_tree_returns_none():
    collection = make_collection(None)

    assert await FamilyTreeRepository.remove_member(OWNER, MEMBER, collection=collection) is None


@pytest.mark.asyncio
async def test_unassigned_member_comes_back_without_a_role():
    collection = make_collection(
        {"user_id": OWNER, "family_members": [{"user_id": MEMBER}]}
    )

    removed = await FamilyTreeRepository.remove_member(OWNER, MEMBER, collection=collection)

    assert removed.family_role is None
