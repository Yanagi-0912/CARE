"""RBAC 遷移差異的計數器。

影子模式每次判定不一致就記一筆 log，但 log 回答不了遷移就緒判準要問的三個
問題：收緊差異佔多少比例、哪些擁有者還在產生差異、以及有多少擁有者真的做過
角色指派。那些要的是**可查詢的聚合**，不是散在檔案裡的字串。

存計數器而不是逐筆事件：

- 逐筆事件會在影子模式期間長成一個與請求量同級的集合，而我們只需要比例與
  名單。真要追某一次判定的細節，log 裡有完整的判定要素。
- 計數器天生就是「可按擁有者分群」——判準要問的是「這位擁有者能不能進入
  強制」，那是逐家庭的問題，不是全體平均值。

**這裡只存數字，不存任何判斷。** 「多少算夠低」是部署決策，不寫進 repo。
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.mongodb import MongoDBManager

logger = logging.getLogger(__name__)

# 差異的兩個方向，意義完全不同：
#   tighten —— legacy 允許、RBAC 拒絕。遷移的成本，數量決定切換時機。
#   loosen  —— legacy 拒絕、RBAC 允許。不該存在，是角色解析或矩陣有錯的訊號。
DIRECTIONS = ("tighten", "loosen")


class FamilyRbacMetricsRepository:
    """family_rbac_metrics collection 的操作。一份文件對應一位資料擁有者。"""

    @staticmethod
    def _collection(collection: Optional[Any] = None):
        if collection is not None:
            return collection
        return MongoDBManager.get_database()["family_rbac_metrics"]

    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        col = FamilyRbacMetricsRepository._collection(collection)
        # 一位擁有者一份文件；$inc 靠這個唯一鍵定位。
        await col.create_index("owner_id", unique=True)
        # 判準 4「仍在產生收緊差異的擁有者可逐一列舉」的查詢形狀。
        await col.create_index("tighten")

    @staticmethod
    async def record(
        owner_id: str,
        direction: str,
        now: Optional[datetime] = None,
        collection: Optional[Any] = None,
    ) -> None:
        """把一次差異記進計數器。

        `decisions` 一併累加：判準 1 要的是**比例**，只有分子沒有分母算不出來。
        """
        if direction not in DIRECTIONS:
            raise ValueError(f"不支援的差異方向：{direction}。可用值：{list(DIRECTIONS)}")

        col = FamilyRbacMetricsRepository._collection(collection)
        moment = now or datetime.now(tz=timezone.utc)
        await col.update_one(
            {"owner_id": owner_id},
            {
                "$inc": {direction: 1},
                "$set": {"last_diff_at": moment},
                "$setOnInsert": {"owner_id": owner_id, "first_diff_at": moment},
            },
            upsert=True,
        )

    @staticmethod
    async def record_decision(
        owner_id: str,
        now: Optional[datetime] = None,
        collection: Optional[Any] = None,
    ) -> None:
        """累加一次授權判定（不論是否有差異），作為比例的分母。"""
        col = FamilyRbacMetricsRepository._collection(collection)
        moment = now or datetime.now(tz=timezone.utc)
        await col.update_one(
            {"owner_id": owner_id},
            {
                "$inc": {"decisions": 1},
                "$setOnInsert": {"owner_id": owner_id, "first_diff_at": moment},
            },
            upsert=True,
        )

    @staticmethod
    async def get(
        owner_id: str, collection: Optional[Any] = None
    ) -> Dict[str, Any]:
        """單一擁有者的計數。查無紀錄時回全零，而不是 None——呼叫端要算比例，
        「沒有差異」與「沒有這個人」在數字上是同一件事。"""
        col = FamilyRbacMetricsRepository._collection(collection)
        doc = await col.find_one({"owner_id": owner_id})
        return FamilyRbacMetricsRepository._normalize(owner_id, doc)

    @staticmethod
    async def list_owners_with_tighten(
        collection: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """仍在產生收緊差異的擁有者，由多到少。

        判準 4 存在的理由：3% 的收緊差異若集中在 3 位重度使用者身上，和平均
        散布在 300 位偶爾使用者身上，是完全不同的兩件事——前者應該先去問那
        3 位，而一個孤立的百分比說不出這個差別。
        """
        col = FamilyRbacMetricsRepository._collection(collection)
        cursor = col.find({"tighten": {"$gt": 0}}).sort("tighten", -1)
        docs = await cursor.to_list(length=None)
        return [
            FamilyRbacMetricsRepository._normalize(doc.get("owner_id"), doc)
            for doc in docs
        ]

    @staticmethod
    async def totals(collection: Optional[Any] = None) -> Dict[str, int]:
        """全體彙總。它回答「這波 rollout 值不值得推」，**不是**單一擁有者的
        准入條件——後者一律看該擁有者自己的數字。"""
        col = FamilyRbacMetricsRepository._collection(collection)
        cursor = col.aggregate(
            [
                {
                    "$group": {
                        "_id": None,
                        "tighten": {"$sum": {"$ifNull": ["$tighten", 0]}},
                        "loosen": {"$sum": {"$ifNull": ["$loosen", 0]}},
                        "decisions": {"$sum": {"$ifNull": ["$decisions", 0]}},
                        "owners": {"$sum": 1},
                    }
                }
            ]
        )
        docs = await cursor.to_list(length=1)
        if not docs:
            return {"tighten": 0, "loosen": 0, "decisions": 0, "owners": 0}
        doc = docs[0]
        return {
            "tighten": doc.get("tighten", 0),
            "loosen": doc.get("loosen", 0),
            "decisions": doc.get("decisions", 0),
            "owners": doc.get("owners", 0),
        }

    @staticmethod
    def _normalize(owner_id: Optional[str], doc: Optional[Dict]) -> Dict[str, Any]:
        doc = doc or {}
        return {
            "owner_id": owner_id,
            "tighten": doc.get("tighten", 0),
            "loosen": doc.get("loosen", 0),
            "decisions": doc.get("decisions", 0),
            "last_diff_at": doc.get("last_diff_at"),
        }
