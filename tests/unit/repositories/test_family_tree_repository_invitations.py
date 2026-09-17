"""FamilyTreeRepository 的邀請狀態轉換：接受是原子的、撤銷只動 pending。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo import ReturnDocument

from app.repositories.family_tree_repository import FamilyTreeRepository

CODE = "tok-123"


def _doc(status="accepted"):
    from datetime import datetime, timezone

    return {
        "_id": CODE,
        "inviter_id": "U-inviter",
        "owner_id": "U-owner",
        "status": status,
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc),
    }


@pytest.mark.asyncio
async def test_accept_claims_only_a_pending_invitation():
    collection = MagicMock()
    collection.find_one_and_update = AsyncMock(return_value=_doc())

    claimed = await FamilyTreeRepository.accept_invitation(
        CODE, accepted_by="U-invitee", collection=collection
    )

    assert claimed is not None and claimed.status == "accepted"
    query, update = collection.find_one_and_update.await_args.args
    assert query == {"_id": CODE, "status": "pending"}
    assert update["$set"]["status"] == "accepted"
    assert update["$set"]["accepted_by"] == "U-invitee"
    assert collection.find_one_and_update.await_args.kwargs["return_document"] is ReturnDocument.AFTER


@pytest.mark.asyncio
async def test_accept_returns_none_when_someone_else_claimed_it():
    collection = MagicMock()
    collection.find_one_and_update = AsyncMock(return_value=None)

    assert await FamilyTreeRepository.accept_invitation(CODE, collection=collection) is None


@pytest.mark.asyncio
async def test_revoke_marks_only_a_pending_invitation():
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

    assert await FamilyTreeRepository.revoke_invitation(CODE, revoked_by="U-owner", collection=collection) is True
    query, update = collection.update_one.await_args.args
    assert query == {"_id": CODE, "status": "pending"}
    assert update["$set"]["status"] == "revoked"
    assert update["$set"]["revoked_by"] == "U-owner"


@pytest.mark.asyncio
async def test_revoke_reports_false_when_nothing_changed():
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=MagicMock(modified_count=0))

    assert await FamilyTreeRepository.revoke_invitation(CODE, revoked_by="U-owner", collection=collection) is False
