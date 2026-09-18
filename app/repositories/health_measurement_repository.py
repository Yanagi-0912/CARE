"""血壓／血糖量測 (health_measurements) 的資料庫操作。

只負責持久化——輸入範圍檢查在 ``app/models/health.py`` 的兩個 Create 請求
模型；等級判定、代記授權、跨使用者遮罩都在服務／路由層（Task 3 以後）。
"""

from datetime import datetime
from typing import Any, List, Optional

from bson import ObjectId

from app.db.mongodb import MongoDBManager
from app.models.health import HealthMeasurement, MeasurementKind

# 查詢單次回應 SHALL NOT 超過 200 筆（constraints「狀態碼」段）。
MAX_QUERY_RESULTS = 200


class HealthMeasurementRepository:
    """``health_measurements`` collection 的操作。"""

    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        if collection is None:
            collection = MongoDBManager.get_health_measurements_collection()
        # (user_id, kind, measured_at desc)：依人、依類型查歷史紀錄，依量測時間
        # 新到舊排序的查詢形狀（design.md「資料格式」）。
        await collection.create_index(
            [("user_id", 1), ("kind", 1), ("measured_at", -1)]
        )

    @staticmethod
    async def add(
        measurement: HealthMeasurement, collection: Optional[Any] = None
    ) -> HealthMeasurement:
        if collection is None:
            collection = MongoDBManager.get_health_measurements_collection()
        # exclude_none：血壓紀錄沒有 glucose_mg_dl／meal_context，血糖紀錄沒有
        # systolic／diastolic／pulse，兩種都不該把不相關的欄位以 null 寫進去。
        doc = measurement.model_dump(by_alias=True, exclude_none=True)
        if not doc.get("_id"):
            doc["_id"] = str(ObjectId())
        await collection.insert_one(doc)
        doc["_id"] = str(doc["_id"])
        return HealthMeasurement(**doc)

    @staticmethod
    async def list_by_user(
        user_id: str,
        kind: Optional[MeasurementKind] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        limit: int = MAX_QUERY_RESULTS,
        collection: Optional[Any] = None,
    ) -> List[HealthMeasurement]:
        """依使用者、選填的類型與量測時間區間查詢，依量測時間新到舊排序。

        區間與「未指定時回傳最近 30 天」由呼叫端（服務層）決定要不要帶
        ``start``／``end``；這裡只負責把帶進來的條件轉成查詢，並把單次回應
        上限（預設 200 筆，constraints「狀態碼」段）當成安全網。
        """
        if collection is None:
            collection = MongoDBManager.get_health_measurements_collection()
        query: dict = {"user_id": user_id}
        if kind is not None:
            query["kind"] = kind
        if start is not None or end is not None:
            range_query: dict = {}
            if start is not None:
                range_query["$gte"] = start
            if end is not None:
                range_query["$lte"] = end
            query["measured_at"] = range_query

        cursor = collection.find(query).sort("measured_at", -1).limit(limit)
        docs = await cursor.to_list(length=None)
        return [
            HealthMeasurement(**{**doc, "_id": str(doc["_id"])}) for doc in docs
        ]

    @staticmethod
    async def get_by_id(
        measurement_id: str, collection: Optional[Any] = None
    ) -> Optional[HealthMeasurement]:
        if collection is None:
            collection = MongoDBManager.get_health_measurements_collection()
        doc = await collection.find_one({"_id": measurement_id})
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return HealthMeasurement(**doc)

    @staticmethod
    async def delete(
        measurement_id: str, collection: Optional[Any] = None
    ) -> bool:
        if collection is None:
            collection = MongoDBManager.get_health_measurements_collection()
        result = await collection.delete_one({"_id": measurement_id})
        return result.deleted_count > 0
