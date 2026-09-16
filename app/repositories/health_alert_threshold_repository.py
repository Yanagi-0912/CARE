"""血壓／血糖提醒範圍 (health_alert_thresholds) 的資料庫操作。

一位使用者一份文件，唯一鍵是 ``user_id``（不是 ``_id``）；沒有文件等同全部
未設定（design.md「資料格式」）。
"""

from typing import Any, Optional

from app.db.mongodb import MongoDBManager
from app.models.health import HealthAlertThreshold


class HealthAlertThresholdRepository:
    """``health_alert_thresholds`` collection 的操作。"""

    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        if collection is None:
            collection = MongoDBManager.get_health_alert_thresholds_collection()
        await collection.create_index("user_id", unique=True)

    @staticmethod
    async def get(
        user_id: str, collection: Optional[Any] = None
    ) -> Optional[HealthAlertThreshold]:
        if collection is None:
            collection = MongoDBManager.get_health_alert_thresholds_collection()
        doc = await collection.find_one({"user_id": user_id})
        if not doc:
            return None
        return HealthAlertThreshold(**doc)

    @staticmethod
    async def upsert(
        threshold: HealthAlertThreshold, collection: Optional[Any] = None
    ) -> HealthAlertThreshold:
        """以 ``user_id`` 為鍵整份覆寫（六個範圍欄位 + updated_by/updated_at）。

        刻意不用 ``exclude_none``：使用者清除已設定的一項（例如把舒張壓上限
        清為空）必須真的把該欄位 ``$set`` 成 ``null``，而不是被濾掉、留在
        資料庫裡的舊值繼續生效（health-alerts spec「清除一項」）。
        """
        if collection is None:
            collection = MongoDBManager.get_health_alert_thresholds_collection()
        update_doc = threshold.model_dump(exclude={"user_id"})
        await collection.update_one(
            {"user_id": threshold.user_id},
            {"$set": update_doc},
            upsert=True,
        )
        doc = await collection.find_one({"user_id": threshold.user_id})
        return HealthAlertThreshold(**doc)
