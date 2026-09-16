"""走失求救的即時位置分享紀錄（lost_sessions collection）。

一份文件是一次求救：從長輩說「我走丟了」開始，到家人按「已找到」、長輩按「我
已經安全了」或時間到自動結束為止。

為什麼放 Mongo，不沿用 UserLocationRepository：那是單一 process 記憶體裡的
快取。長輩上傳位置打到的 API pod、家人查地圖打到的 API pod、檢查位置停止更新
的 scheduler pod 是不同的行程，三邊必須看到同一份資料。

保存期限：`purge_at`（開始後 24 小時）由 TTL 索引刪除。位置是行蹤資料，不該
無限期留著；但結束後家人可能還要回頭看最後位置、跟警察說明，所以不是一結束就刪。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db.mongodb import MongoDBManager

ACTIVE = "active"

# 軌跡最多留幾個點。長輩頁每 20 秒上傳一次，自動結束是 2 小時（見
# lost_location_service），2 小時 × 每分鐘 3 點 = 360，剛好放得下一整次。
TRAIL_MAX_POINTS = 360


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Motor 沒開 tz_aware，讀回來的 datetime 是 naive（實際是 UTC）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class LostSessionRepository:
    @staticmethod
    def _collection(collection: Optional[Any]) -> Any:
        if collection is not None:
            return collection
        return MongoDBManager.get_lost_sessions_collection()

    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        collection = LostSessionRepository._collection(collection)
        # 同一位長輩同時只能有一次進行中的求救。說了兩次「我走丟了」、或兩個
        # webhook 併發進來，都要落到同一份文件，家人才不會收到兩張卡、看兩張地圖。
        await collection.create_index(
            "user_id",
            unique=True,
            partialFilterExpression={"status": ACTIVE},
            name="one_active_per_user",
        )
        await collection.create_index([("user_id", 1), ("started_at", -1)])
        # scheduler 每分鐘掃「位置停止更新」與「時間到」的兩個查詢。
        await collection.create_index([("status", 1), ("last_seen_at", 1)])
        await collection.create_index([("status", 1), ("auto_end_at", 1)])
        await collection.create_index("purge_at", expireAfterSeconds=0)

    @staticmethod
    async def create_active(
        document: dict[str, Any], collection: Optional[Any] = None
    ) -> tuple[dict[str, Any], bool]:
        """建立一次進行中的求救。已經有一次進行中時回傳那一次，第二個值為 False。

        不先查再寫：兩則「我走丟了」併發時兩邊都會查到「沒有」而各建一份。
        撞上唯一索引就代表另一邊先建好了。
        """
        collection = LostSessionRepository._collection(collection)
        try:
            await collection.insert_one(dict(document))
            return document, True
        except DuplicateKeyError:
            existing = await collection.find_one(
                {"user_id": document["user_id"], "status": ACTIVE}
            )
            if existing is None:
                # 唯一索引擋下之後、查詢之前，那一次剛好被結束了。極少見；
                # 再建一次就好，不需要迴圈——再撞一次代表真的有人正在建。
                await collection.insert_one(dict(document))
                return document, True
            return existing, False

    @staticmethod
    async def find_active(
        user_id: str, collection: Optional[Any] = None
    ) -> Optional[dict[str, Any]]:
        collection = LostSessionRepository._collection(collection)
        return await collection.find_one({"user_id": user_id, "status": ACTIVE})

    @staticmethod
    async def find_latest(
        user_id: str, collection: Optional[Any] = None
    ) -> Optional[dict[str, Any]]:
        """最近一次求救，不論是否已結束。家人的地圖頁在結束後仍要看得到最後位置。"""
        collection = LostSessionRepository._collection(collection)
        return await collection.find_one(
            {"user_id": user_id}, sort=[("started_at", -1)]
        )

    @staticmethod
    async def append_location(
        user_id: str,
        point: dict[str, Any],
        received_at: datetime,
        collection: Optional[Any] = None,
    ) -> Optional[dict[str, Any]]:
        """寫入最新位置並接到軌跡尾端。沒有進行中的求救時回傳 None。

        `stale_notified_at` 清回 None：位置恢復更新之後若又停了，家人要再被通知
        一次。
        """
        collection = LostSessionRepository._collection(collection)
        return await collection.find_one_and_update(
            {"user_id": user_id, "status": ACTIVE},
            {
                "$set": {
                    "last_location": point,
                    "last_seen_at": received_at,
                    "stale_notified_at": None,
                },
                "$push": {
                    "trail": {
                        "$each": [
                            {
                                "lat": point["lat"],
                                "lng": point["lng"],
                                "at": received_at,
                            }
                        ],
                        "$slice": -TRAIL_MAX_POINTS,
                    }
                },
            },
            return_document=ReturnDocument.AFTER,
        )

    @staticmethod
    async def claim_notice(
        session_id: str,
        field: str,
        now: datetime,
        extra_filter: Optional[dict[str, Any]] = None,
        collection: Optional[Any] = None,
    ) -> bool:
        """原子取得某一則通知的推播權（欄位從 None 變成 now 的那一方才推）。

        API pod 與 scheduler pod 可能同時看到同一個狀態；滾動更新期間也會有兩個
        scheduler 並存。沒有這一步，家人會收到兩則一模一樣的通知。
        """
        collection = LostSessionRepository._collection(collection)
        query: dict[str, Any] = {"session_id": session_id, field: None}
        if extra_filter:
            query.update(extra_filter)
        result = await collection.update_one(query, {"$set": {field: now}})
        return result.modified_count == 1

    @staticmethod
    async def end_active(
        user_id: str,
        status: str,
        ended_by: str,
        now: datetime,
        extra_filter: Optional[dict[str, Any]] = None,
        collection: Optional[Any] = None,
    ) -> Optional[dict[str, Any]]:
        """結束進行中的求救。已經結束（或從沒開始）時回傳 None。

        條件式更新本身就是結束通知的推播權：家人與長輩同時按下結束，只有一方
        拿得到文件，另一方拿到 None、不會再推一次。
        """
        collection = LostSessionRepository._collection(collection)
        query: dict[str, Any] = {"user_id": user_id, "status": ACTIVE}
        if extra_filter:
            query.update(extra_filter)
        return await collection.find_one_and_update(
            query,
            {"$set": {"status": status, "ended_at": now, "ended_by": ended_by}},
            return_document=ReturnDocument.AFTER,
        )

    @staticmethod
    async def find_stale(
        cutoff: datetime, collection: Optional[Any] = None
    ) -> list[dict[str, Any]]:
        """傳過位置、但 cutoff 之後就沒再更新、還沒通知過的進行中求救。

        從沒傳過位置的不算：那是長輩還沒打開頁面，家人收到的第一張卡已經寫著
        「正在等他傳位置」。
        """
        collection = LostSessionRepository._collection(collection)
        cursor = collection.find(
            {
                "status": ACTIVE,
                "last_seen_at": {"$ne": None, "$lt": cutoff},
                "stale_notified_at": None,
            }
        )
        return await cursor.to_list(length=None)

    @staticmethod
    async def find_due_to_end(
        now: datetime, collection: Optional[Any] = None
    ) -> list[dict[str, Any]]:
        collection = LostSessionRepository._collection(collection)
        cursor = collection.find({"status": ACTIVE, "auto_end_at": {"$lte": now}})
        return await cursor.to_list(length=None)
