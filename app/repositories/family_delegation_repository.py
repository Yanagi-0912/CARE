"""受委任 GUARDIAN 的授權紀錄。

委任是本系統唯一一條「不經資料擁有者同意就取得其資料權限」的路徑，因此這裡
的每一個決定都偏向保守：

- 有效性由 `revoked_at` 與 `expires_at` 兩個欄位共同決定，查詢時就篩掉失效的
  紀錄，呼叫端拿到什麼就是什麼，不必自己再判一次（少一個判錯的地方）。
- 撤銷與到期一律**不刪除文件**：委任存續的那段期間正是最需要事後查得到的
  一段，紀錄若隨委任消失，失去的正是最該保留的那段。
- 沒有「不到期」這個選項：`expires_at` 必填，預設 90 天。

與 family_trees 分開存放的理由見 `MongoDBManager.get_family_delegations_collection`。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional

from app.db.mongodb import MongoDBManager
from app.models.family_tree import (
    DELEGATION_DEFAULT_VALID_DAYS,
    FamilyDelegation,
)

logger = logging.getLogger(__name__)


class FamilyDelegationRepository:
    """family_delegations collection 的操作。"""

    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        if collection is None:
            collection = MongoDBManager.get_family_delegations_collection()
        # 授權判定每次都要查「這位操作者對這位擁有者有沒有有效委任」，
        # (owner_id, delegate_user_id) 是唯一的查詢形狀。
        await collection.create_index([("owner_id", 1), ("delegate_user_id", 1)])

        # 刻意 **不** 建 TTL 索引：到期的委任要留著供稽核，不能讓資料庫自動
        # 清掉。過期與否由查詢時比對 expires_at 決定（見 list_active），
        # 而不是靠文件消失——這與 safety_alerts 的節流紀錄相反，那裡的紀錄
        # 過期就沒有保留價值。

    @staticmethod
    async def grant(
        owner_id: str,
        delegate_user_id: str,
        granted_by: str,
        approval_ref: Optional[str] = None,
        valid_days: int = DELEGATION_DEFAULT_VALID_DAYS,
        now: Optional[datetime] = None,
        collection: Optional[Any] = None,
    ) -> FamilyDelegation:
        """建立一筆委任。

        `now` 可注入，讓測試不必等待真實時間流逝就能構造到期情境
        （沿用專案「以依賴注入取代 monkey patch」的測試慣例）。

        本方法只負責寫入，**不判斷申請資格、也不執行核可流程**——那道閘門
        由服務層把關，其內容待後續的產品／法務 change 定義。
        """
        if valid_days <= 0:
            raise ValueError("委任效期必須大於零天：委任 SHALL NOT 永久存在，也不得零效期")

        if collection is None:
            collection = MongoDBManager.get_family_delegations_collection()
        moment = now or datetime.now(tz=timezone.utc)

        delegation = FamilyDelegation(
            owner_id=owner_id,
            delegate_user_id=delegate_user_id,
            granted_at=moment,
            granted_by=granted_by,
            expires_at=moment + timedelta(days=valid_days),
            approval_ref=approval_ref,
        )
        await collection.insert_one(delegation.model_dump())
        logger.info(
            "委任已建立：owner=%s, delegate=%s, expires_at=%s",
            owner_id,
            delegate_user_id,
            delegation.expires_at.isoformat(),
        )
        return delegation

    @staticmethod
    async def list_active(
        owner_id: str,
        now: Optional[datetime] = None,
        collection: Optional[Any] = None,
    ) -> List[FamilyDelegation]:
        """列出某位擁有者當下有效的委任。

        已撤銷與已到期的一律不回傳——授權判定拿到的清單就是可以直接用的，
        呼叫端不需要、也不應該再自己過濾一次。
        """
        if collection is None:
            collection = MongoDBManager.get_family_delegations_collection()
        moment = now or datetime.now(tz=timezone.utc)

        cursor = collection.find(
            {
                "owner_id": owner_id,
                "revoked_at": None,
                "expires_at": {"$gt": moment},
            }
        )
        docs = await cursor.to_list(length=None)
        return [FamilyDelegation(**doc) for doc in docs]

    @staticmethod
    async def has_active_delegation(
        owner_id: str,
        delegate_user_id: str,
        now: Optional[datetime] = None,
        collection: Optional[Any] = None,
    ) -> bool:
        """某位操作者對某位擁有者是否持有有效委任。

        這是授權判定的熱路徑，因此查單筆而不是把整份清單撈回來過濾。
        """
        if collection is None:
            collection = MongoDBManager.get_family_delegations_collection()
        moment = now or datetime.now(tz=timezone.utc)

        doc = await collection.find_one(
            {
                "owner_id": owner_id,
                "delegate_user_id": delegate_user_id,
                "revoked_at": None,
                "expires_at": {"$gt": moment},
            }
        )
        return doc is not None

    @staticmethod
    async def list_delegated_owner_ids(
        delegate_user_id: str,
        owner_ids: List[str],
        now: Optional[datetime] = None,
        collection: Optional[Any] = None,
    ) -> List[str]:
        """在給定的擁有者裡，哪幾位對這位操作者有**有效**委任。單一查詢。

        供族譜頁一次算出每位成員的權限用——逐位詢問 has_active_delegation
        會退化成 N 次往返。有效性一樣在查詢條件裡就成立。
        """
        if not owner_ids:
            return []
        if collection is None:
            collection = MongoDBManager.get_family_delegations_collection()
        moment = now or datetime.now(tz=timezone.utc)

        cursor = collection.find(
            {
                "delegate_user_id": delegate_user_id,
                "owner_id": {"$in": owner_ids},
                "revoked_at": None,
                "expires_at": {"$gt": moment},
            },
            {"owner_id": 1},
        )
        docs = await cursor.to_list(length=None)
        return [doc["owner_id"] for doc in docs]

    @staticmethod
    async def revoke(
        owner_id: str,
        delegate_user_id: str,
        revoked_by: str,
        now: Optional[datetime] = None,
        collection: Optional[Any] = None,
    ) -> int:
        """撤銷某位擁有者對某位受委任者的全部有效委任，回傳受影響筆數。

        撤銷是**標記**而非刪除：文件留著，只是 `revoked_at` 有了值。擁有者
        恢復操作能力後撤銷委任期間建立的委任，也走這條路徑——那正是最需要
        留下痕跡的情境。

        已撤銷者不重複標記（條件含 `revoked_at: None`），否則第二次撤銷會把
        時間戳蓋掉，稽核上看起來像是撤銷發生在更晚的時間點。
        """
        if collection is None:
            collection = MongoDBManager.get_family_delegations_collection()
        moment = now or datetime.now(tz=timezone.utc)

        result = await collection.update_many(
            {
                "owner_id": owner_id,
                "delegate_user_id": delegate_user_id,
                "revoked_at": None,
            },
            {"$set": {"revoked_at": moment, "revoked_by": revoked_by}},
        )
        logger.info(
            "委任已撤銷：owner=%s, delegate=%s, count=%s",
            owner_id,
            delegate_user_id,
            result.modified_count,
        )
        return result.modified_count

    @staticmethod
    async def list_all_for_audit(
        owner_id: str,
        collection: Optional[Any] = None,
    ) -> List[FamilyDelegation]:
        """列出某位擁有者的**全部**委任紀錄，含已到期與已撤銷者。

        與 list_active 分成兩支而不是加一個布林參數：授權判定與稽核查詢是
        兩種不同的意圖，讓「要不要含失效的」由方法名回答，呼叫端就不可能
        在授權路徑上不小心傳錯旗標而放行一筆已撤銷的委任。
        """
        if collection is None:
            collection = MongoDBManager.get_family_delegations_collection()
        cursor = collection.find({"owner_id": owner_id})
        docs = await cursor.to_list(length=None)
        return [FamilyDelegation(**doc) for doc in docs]
