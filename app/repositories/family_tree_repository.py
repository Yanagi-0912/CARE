import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.db.mongodb import MongoDBManager
from app.models.family_tree import FamilyMember, FamilyTree, PendingInvitation

logger = logging.getLogger(__name__)

_INVITE_TTL_DAYS = 7
_INVITE_ID_BYTES = 4  # secrets.token_hex(4) → 8 hex chars


class FamilyTreeRepository:
    """封裝所有族譜相關的 MongoDB 操作"""

    # ── FamilyTree ────────────────────────────────────────────────────────────

    @staticmethod
    async def get_by_user_id(user_id: str) -> Optional[FamilyTree]:
        col = MongoDBManager.get_family_tree_collection()
        doc = await col.find_one({"user_id": user_id})
        if doc is None:
            return None
        return FamilyTree(**doc)

    @staticmethod
    async def upsert_tree(user_id: str) -> FamilyTree:
        """取得族譜；若不存在則建立空族譜並回傳。"""
        col = MongoDBManager.get_family_tree_collection()
        now = datetime.now(tz=timezone.utc)
        await col.update_one(
            {"user_id": user_id},
            {
                "$setOnInsert": {
                    "user_id": user_id,
                    "family_members": [],
                    "created_at": now,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
        doc = await col.find_one({"user_id": user_id})
        return FamilyTree(**doc)

    @staticmethod
    async def add_member(user_id: str, member: FamilyMember) -> FamilyTree:
        """將一位成員加入族譜（若已存在則略過）。"""
        col = MongoDBManager.get_family_tree_collection()
        now = datetime.now(tz=timezone.utc)

        # 避免重複加入同一位成員
        await col.update_one(
            {"user_id": user_id, "family_members.user_id": {"$ne": member.user_id}},
            {
                "$push": {"family_members": member.model_dump()},
                "$set": {"updated_at": now},
            },
        )
        doc = await col.find_one({"user_id": user_id})
        return FamilyTree(**doc)

    @staticmethod
    async def set_relationship(
        user_id: str, member_id: str, relationship_type: str
    ) -> Optional[FamilyTree]:
        """更新族譜中特定成員的 relationship_type。"""
        col = MongoDBManager.get_family_tree_collection()
        now = datetime.now(tz=timezone.utc)

        result = await col.update_one(
            {"user_id": user_id, "family_members.user_id": member_id},
            {
                "$set": {
                    "family_members.$.relationship_type": relationship_type,
                    "updated_at": now,
                }
            },
        )
        if result.matched_count == 0:
            logger.warning(
                f"set_relationship: member {member_id} not found in tree of {user_id}"
            )
            return None
        doc = await col.find_one({"user_id": user_id})
        return FamilyTree(**doc)

    # ── PendingInvitation ─────────────────────────────────────────────────────

    @staticmethod
    async def create_invitation(inviter_id: str) -> PendingInvitation:
        """建立一筆新的邀請記錄，使用 8 碼隨機 hex 作為 ID。"""
        col = MongoDBManager.get_pending_invitations_collection()
        now = datetime.now(tz=timezone.utc)
        invite_id = secrets.token_hex(_INVITE_ID_BYTES)  # e.g. "a3f8bc2e"

        doc = {
            "_id": invite_id,
            "inviter_id": inviter_id,
            "status": "pending",
            "created_at": now,
            "expires_at": now + timedelta(days=_INVITE_TTL_DAYS),
        }
        await col.insert_one(doc)
        return PendingInvitation(**doc)

    @staticmethod
    async def get_invitation(invite_id: str) -> Optional[PendingInvitation]:
        col = MongoDBManager.get_pending_invitations_collection()
        doc = await col.find_one({"_id": invite_id})
        if doc is None:
            return None
        return PendingInvitation(**doc)

    @staticmethod
    async def accept_invitation(invite_id: str) -> None:
        """將邀請狀態更新為 accepted。"""
        col = MongoDBManager.get_pending_invitations_collection()
        await col.update_one(
            {"_id": invite_id},
            {"$set": {"status": "accepted"}},
        )
