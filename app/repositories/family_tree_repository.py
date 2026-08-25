import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.mongodb import MongoDBManager
from app.models.family_authorization import ASSIGNABLE_FAMILY_ROLES
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

    @staticmethod
    async def get_roles_for_operator(
        operator_id: str,
        owner_ids: List[str],
        collection: Optional[Any] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """一次查出「操作者在這些擁有者族譜中各是什麼角色」與各自的遷移狀態。

        `GET /api/family/me` 需要兩個**方向相反**的資訊：我的族譜記的是「他們
        對我的資料」的角色，而畫面還要知道「我對他們的資料」能做什麼——後者
        存在對方的文件裡。

        SHALL 以單一查詢完成：族譜頁一次可能有十餘位成員，逐一查詢的延遲在
        長輩的行動網路上是看得見的。

        回傳 `{owner_id: {"family_role": str|None, "rbac_migration_state": str}}`。
        查不到的擁有者不會出現在結果裡——呼叫端據此視為「不是他的家人」，
        而不是給一個預設角色。
        """
        if not owner_ids:
            return {}
        if collection is None:
            collection = MongoDBManager.get_family_tree_collection()

        cursor = collection.find(
            {"user_id": {"$in": owner_ids}},
            {"user_id": 1, "family_members": 1, "rbac_migration_state": 1},
        )
        docs = await cursor.to_list(length=None)

        result: Dict[str, Dict[str, Any]] = {}
        for doc in docs:
            member = next(
                (
                    m
                    for m in (doc.get("family_members") or [])
                    if m.get("user_id") == operator_id
                ),
                None,
            )
            if member is None:
                # 操作者不在這位擁有者的族譜裡。不放進結果——family boundary
                # 是最外層的閘門，缺席不該被解讀成任何角色。
                continue
            result[doc["user_id"]] = {
                "family_role": member.get("family_role"),
                "rbac_migration_state": doc.get("rbac_migration_state", "shadow"),
            }
        return result

    @staticmethod
    async def count_assignment_progress(
        collection: Optional[Any] = None,
    ) -> Dict[str, int]:
        """遷移就緒判準 3 的分子與分母：有族譜成員的擁有者中，有幾位已完成指派。

        「完成」＝該擁有者族譜中**每一位**現有成員都持有明確的 `family_role`。
        欄位缺席即未設定——即使其授權行為與 `MEMBER` 相同。授權上的預設值與
        指派上的完成判定是兩件事，這裡量的是後者。

        判準 3 不可由判準 1（收緊差異降低）取代：差異降到零也可能只是因為沒有
        人在使用這個功能，那不是遷移完成，是沒人受影響地把功能關掉了。要知道
        遷移真的成功，必須看到擁有者實際指派了角色。

        以單一 aggregation 完成，SHALL NOT 逐位擁有者查詢。
        """
        if collection is None:
            collection = MongoDBManager.get_family_tree_collection()

        cursor = collection.aggregate(
            [
                # 沒有成員的擁有者不列入分母：沒有人要指派，不該把他算成
                # 「還沒完成」而拖低比例。
                {"$match": {"family_members.0": {"$exists": True}}},
                {
                    "$project": {
                        "unassigned": {
                            "$size": {
                                "$filter": {
                                    "input": "$family_members",
                                    "as": "member",
                                    "cond": {
                                        "$in": [
                                            {"$ifNull": ["$$member.family_role", None]},
                                            [None, ""],
                                        ]
                                    },
                                }
                            }
                        }
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "owners_with_members": {"$sum": 1},
                        "owners_complete": {
                            "$sum": {"$cond": [{"$eq": ["$unassigned", 0]}, 1, 0]}
                        },
                    }
                },
            ]
        )
        docs = await cursor.to_list(length=1)
        if not docs:
            return {"owners_with_members": 0, "owners_complete": 0}
        return {
            "owners_with_members": docs[0].get("owners_with_members", 0),
            "owners_complete": docs[0].get("owners_complete", 0),
        }

    @staticmethod
    async def set_family_role(
        user_id: str,
        member_id: str,
        family_role: str,
        collection: Optional[Any] = None,
    ) -> Optional[FamilyTree]:
        """指派族譜中特定成員的 family_role。

        `user_id` 恆為**資料擁有者**，`member_id` 只是 `family_members` 陣列裡
        的一個元素——這條路徑不存在「改別人族譜裡的自己」這種形狀。呼叫端要
        代擁有者操作時，資格判定必須在進到這裡之前就完成。

        `OWNER` 在這裡直接擋掉，不倚賴 Pydantic：這支方法會被服務層以字串
        呼叫，而 `$set` 是直接寫進陣列元素的，模型驗證不會在寫入路徑上執行。
        少了這道檢查，一個沒驗過的字串就能把 OWNER 寫進去。

        **SHALL NOT 觸碰 `rbac_migration_state`**：角色指派不得成為讓某個
        家庭退回 legacy 授權的路徑。
        """
        if family_role not in ASSIGNABLE_FAMILY_ROLES:
            raise ValueError(
                f"不可指派的家庭角色：{family_role}。"
                f"可用值：{sorted(ASSIGNABLE_FAMILY_ROLES)}"
            )

        if collection is None:
            collection = MongoDBManager.get_family_tree_collection()
        now = datetime.now(tz=timezone.utc)

        result = await collection.update_one(
            {"user_id": user_id, "family_members.user_id": member_id},
            {
                "$set": {
                    "family_members.$.family_role": family_role,
                    "updated_at": now,
                }
            },
        )
        if result.matched_count == 0:
            logger.warning(
                f"set_family_role: member {member_id} not found in tree of {user_id}"
            )
            return None
        doc = await collection.find_one({"user_id": user_id})
        return FamilyTree(**doc)

    @staticmethod
    async def set_migration_state(
        user_id: str,
        state: str,
        collection: Optional[Any] = None,
    ) -> Optional[FamilyTree]:
        """設定某位擁有者的 RBAC 遷移狀態。

        只有這支方法會動 `rbac_migration_state`。加入成員、指派角色、設定親屬
        關係都 SHALL NOT 碰它——否則「新增家庭成員 → 觸發 legacy fallback」
        就成立了：任何能建立邀請的人只要拉一個新帳號進來，整個家庭就退回變更
        前的寬鬆行為，本能力的所有約束一次全部失效。加入成員是低成本、可重複、
        看起來完全無害的動作，用它當關閉授權的開關是不可接受的。
        """
        if state not in ("shadow", "enforced"):
            raise ValueError(f"不支援的遷移狀態：{state}")

        if collection is None:
            collection = MongoDBManager.get_family_tree_collection()
        now = datetime.now(tz=timezone.utc)

        result = await collection.update_one(
            {"user_id": user_id},
            {"$set": {"rbac_migration_state": state, "updated_at": now}},
        )
        if result.matched_count == 0:
            logger.warning(f"set_migration_state: tree of {user_id} not found")
            return None
        doc = await collection.find_one({"user_id": user_id})
        return FamilyTree(**doc)

    @staticmethod
    async def set_care_recipient(
        user_id: str, member_id: str, is_care_recipient: bool
    ) -> Optional[FamilyTree]:
        """更新族譜中特定成員的 is_care_recipient 標籤。"""
        col = MongoDBManager.get_family_tree_collection()
        now = datetime.now(tz=timezone.utc)

        result = await col.update_one(
            {"user_id": user_id, "family_members.user_id": member_id},
            {
                "$set": {
                    "family_members.$.is_care_recipient": is_care_recipient,
                    "updated_at": now,
                }
            },
        )
        if result.matched_count == 0:
            logger.warning(
                f"set_care_recipient: member {member_id} not found in tree of {user_id}"
            )
            return None
        doc = await col.find_one({"user_id": user_id})
        return FamilyTree(**doc)


    # ── PendingInvitation ─────────────────────────────────────────────────────

    @staticmethod
    async def save_invitation(
        token: str,
        inviter_id: str,
        expires_at: datetime,
        owner_id: Optional[str] = None,
        family_role: Optional[str] = None,
        collection: Optional[Any] = None,
    ) -> PendingInvitation:
        """根據 Service 層提供的資訊儲存邀請記錄。

        `owner_id` 與 `family_role` 在**建立當下**就落地。它們 SHALL NOT 由
        接受邀請的請求攜帶：邀請連結可以被轉發，角色若由接受方帶上來，取得
        連結的人就能自選角色。資格判定同樣在建立當下完成，這裡只負責寫入。
        """
        if collection is None:
            collection = MongoDBManager.get_pending_invitations_collection()
        now = datetime.now(tz=timezone.utc)

        doc = {
            "_id": token,
            "inviter_id": inviter_id,
            "owner_id": owner_id or inviter_id,
            "family_role": family_role,
            "status": "pending",
            "created_at": now,
            "expires_at": expires_at,
        }
        await collection.insert_one(doc)
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
