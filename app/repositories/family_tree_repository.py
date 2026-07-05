import logging
from datetime import datetime, timezone
from typing import Optional

from app.db.mongodb import MongoDBManager
from app.models.family_tree import FamilyMember, FamilyTree, PendingInvitation

logger = logging.getLogger(__name__)


class FamilyTreeRepository:
    """
    封裝所有族譜相關的 MongoDB 操作
    """

    @staticmethod
    async def get_by_user_id(user_id: str) -> Optional[FamilyTree]:
        """
        透過 MongoDB Aggregation ($lookup) 關聯查詢，直接取得包含成員個人資料的族譜。
        """
        col = MongoDBManager.get_family_tree_collection()
        pipeline = [
            {"$match": {"user_id": user_id}},
            {
                "$lookup": {
                    "from": "users",
                    "localField": "family_members.user_id",
                    "foreignField": "line_id",
                    "as": "member_profiles",
                }
            },
            {
                "$addFields": {
                    "family_members": {
                        "$map": {
                            "input": "$family_members",
                            "as": "member",
                            "in": {
                                "$mergeObjects": [
                                    "$$member",
                                    {
                                        "$let": {
                                            "vars": {
                                                "prof": {
                                                    "$arrayElemAt": [
                                                        {
                                                            "$filter": {
                                                                "input": "$member_profiles",
                                                                "as": "p",
                                                                "cond": {
                                                                    "$eq": [
                                                                        "$$p.line_id",
                                                                        "$$member.user_id",
                                                                    ]
                                                                },
                                                            }
                                                        },
                                                        0,
                                                    ]
                                                }
                                            },
                                            "in": {
                                                "display_name": {
                                                    "$ifNull": ["$$prof.name", None]
                                                },
                                                "picture_url": {
                                                    "$ifNull": [
                                                        "$$prof.picture_url",
                                                        None,
                                                    ]
                                                },
                                            },
                                        }
                                    },
                                ]
                            },
                        }
                    }
                }
            },
            {"$project": {"member_profiles": 0}},
        ]
        cursor = col.aggregate(pipeline)
        docs = await cursor.to_list(length=1)
        if not docs:
            return None
        return FamilyTree(**docs[0])

    @staticmethod
    async def upsert_tree(user_id: str) -> FamilyTree:
        """
        取得族譜；若不存在則建立空族譜並回傳。
        """
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
    async def save_invitation(
        token: str, inviter_id: str, expires_at: datetime
    ) -> PendingInvitation:
        """根據 Service 層提供的資訊儲存邀請記錄。"""
        col = MongoDBManager.get_pending_invitations_collection()
        now = datetime.now(tz=timezone.utc)

        doc = {
            "_id": token,
            "inviter_id": inviter_id,
            "status": "pending",
            "created_at": now,
            "expires_at": expires_at,
        }
        await col.insert_one(doc)
        return PendingInvitation(**doc)

    @staticmethod
    async def get_invitation(invite_id: str) -> Optional[PendingInvitation]:
        col = MongoDBManager.get_pending_invitations_collection()
        pipeline = [
            {"$match": {"_id": invite_id}},
            {
                "$lookup": {
                    "from": "users",
                    "localField": "inviter_id",
                    "foreignField": "line_id",
                    "as": "inviter_profile",
                }
            },
            {
                "$addFields": {
                    "inviter_display_name": {
                        "$ifNull": [
                            {"$arrayElemAt": ["$inviter_profile.name", 0]},
                            "家人",
                        ]
                    }
                }
            },
            {"$project": {"inviter_profile": 0}},
        ]
        cursor = col.aggregate(pipeline)
        docs = await cursor.to_list(length=1)
        if not docs:
            return None
        return PendingInvitation(**docs[0])

    @staticmethod
    async def accept_invitation(invite_id: str) -> None:
        """將邀請狀態更新為 accepted。"""
        col = MongoDBManager.get_pending_invitations_collection()
        await col.update_one(
            {"_id": invite_id},
            {"$set": {"status": "accepted"}},
        )
