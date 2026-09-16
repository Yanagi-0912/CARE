"""計步工作階段 (step_sessions) 的資料庫操作。

前端回報的是工作階段目前的累計值，SHALL NOT 是增量（step-counter spec
「同步的冪等性」）；行動網路常斷線重送，因此以 ``$max`` 保留每個工作階段
收過的最大值，重送同一個值或亂序送達較小的值都不會讓步數變小。每日步數是
當日所有工作階段累計值的總和，用 aggregate 在資料庫端加總。
"""

from datetime import datetime, timezone
from typing import Any, Optional

from app.db.mongodb import MongoDBManager
from app.models.health import StepCount, StepSession


class StepSessionRepository:
    """``step_sessions`` collection 的操作。"""

    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        if collection is None:
            collection = MongoDBManager.get_step_sessions_collection()
        # (user_id, session_id) 唯一：sync_progress 的 upsert 靠這個鍵定位，
        # 同一個工作階段永遠只有一份文件。
        await collection.create_index(
            [("user_id", 1), ("session_id", 1)], unique=True
        )
        # (user_id, date)：get_daily_total 依人、依日期彙總的查詢形狀。
        await collection.create_index([("user_id", 1), ("date", 1)])

    @staticmethod
    async def sync_progress(
        user_id: str,
        session_id: str,
        date_str: str,
        steps: int,
        started_at: datetime,
        now: Optional[datetime] = None,
        collection: Optional[Any] = None,
    ) -> StepSession:
        """以 (user_id, session_id) 為唯一鍵 upsert，``$max`` 保留最大累計值。

        ``$setOnInsert`` 只在真的建立新工作階段時寫入 ``date``／``started_at``
        ——同一個工作階段的日期與開始時間在第一次同步就確定，後續同步不該
        因為重新帶了這兩個參數而被覆蓋。
        """
        if collection is None:
            collection = MongoDBManager.get_step_sessions_collection()
        moment = now or datetime.now(timezone.utc)
        await collection.update_one(
            {"user_id": user_id, "session_id": session_id},
            {
                "$max": {"steps": steps},
                "$set": {"last_synced_at": moment},
                "$setOnInsert": {
                    "user_id": user_id,
                    "session_id": session_id,
                    "date": date_str,
                    "started_at": started_at,
                },
            },
            upsert=True,
        )
        doc = await collection.find_one(
            {"user_id": user_id, "session_id": session_id}
        )
        return StepSession(**doc)

    @staticmethod
    async def get_daily_total(
        user_id: str, date_str: str, collection: Optional[Any] = None
    ) -> StepCount:
        """當日步數＝當日所有工作階段累計值的總和（step-counter spec
        「同步的冪等性」）。查無任何工作階段時回傳 0 步，不是 None——這是
        「今天還沒開始計步」與「今天走了 0 步」目前無法區分的已知限制，
        由呼叫端（服務層）決定是否需要另外揭露。
        """
        if collection is None:
            collection = MongoDBManager.get_step_sessions_collection()
        cursor = collection.aggregate(
            [
                {"$match": {"user_id": user_id, "date": date_str}},
                {"$group": {"_id": None, "total": {"$sum": "$steps"}}},
            ]
        )
        docs = await cursor.to_list(length=1)
        total = docs[0]["total"] if docs else 0
        return StepCount(user_id=user_id, date=date_str, steps=total)
