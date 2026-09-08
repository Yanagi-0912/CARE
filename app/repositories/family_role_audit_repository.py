"""角色與委任變更的稽核紀錄。

僅可追加：這個類別**沒有**任何更新或刪除的方法，那是刻意的。指派 GUARDIAN
是本系統唯一一個「一次點擊就讓某人讀得到長輩全部對話」的操作，委任更是不經
擁有者同意就發生的授權；出事時要能回答「誰在什麼時候給了誰權限」，而一份
可以被改寫的紀錄回答不了這個問題。
"""

import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from app.db.mongodb import MongoDBManager
from app.models.family_tree import FamilyRoleAuditEntry

logger = logging.getLogger(__name__)


class FamilyRoleAuditRepository:
    """family_role_audit collection 的操作（append-only）。"""

    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        if collection is None:
            collection = MongoDBManager.get_family_role_audit_collection()
        # 稽核查詢一律以擁有者為起點，再依時間排序還原事件順序。
        await collection.create_index([("owner_id", 1), ("changed_at", -1)])

    @staticmethod
    async def append(
        owner_id: str,
        member_id: str,
        changed_by: str,
        from_role: Optional[str] = None,
        to_role: Optional[str] = None,
        via_delegation: bool = False,
        event: str = "role_change",
        now: Optional[datetime] = None,
        collection: Optional[Any] = None,
    ) -> FamilyRoleAuditEntry:
        """追加一筆稽核紀錄。

        `via_delegation` 必填意義重大：事後要分得出「長輩自己指派的」與
        「別人代他指派的」，那是兩件性質完全不同的授權。
        """
        if collection is None:
            collection = MongoDBManager.get_family_role_audit_collection()

        entry = FamilyRoleAuditEntry(
            owner_id=owner_id,
            member_id=member_id,
            from_role=from_role,
            to_role=to_role,
            changed_at=now or datetime.now(tz=timezone.utc),
            changed_by=changed_by,
            via_delegation=via_delegation,
            event=event,
        )
        await collection.insert_one(entry.model_dump())
        return entry

    @staticmethod
    async def list_for_owner(
        owner_id: str,
        collection: Optional[Any] = None,
    ) -> List[FamilyRoleAuditEntry]:
        """列出某位擁有者的全部稽核紀錄，由新到舊。"""
        if collection is None:
            collection = MongoDBManager.get_family_role_audit_collection()
        cursor = collection.find({"owner_id": owner_id}).sort("changed_at", -1)
        docs = await cursor.to_list(length=None)
        return [FamilyRoleAuditEntry(**doc) for doc in docs]
