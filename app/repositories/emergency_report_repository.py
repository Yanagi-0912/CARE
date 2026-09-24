"""緊急回報的稽核紀錄（emergency_reports）。

僅可追加：這個類別沒有更新或刪除的方法。替別人回報緊急事件不需要任何健康資料
權限，只要有家庭連結就能觸發對方照顧者的推播；出事時（誤報驚嚇、疑似濫用）要能
回答「誰、在什麼時候、替誰回報、通知了誰、結果如何」。

保存 60 天，由 `expires_at` 的 TTL 索引自動刪除（2026-09-25 決定）。
同一份紀錄也是頻率限制的計數來源（見 emergency_alert_service）。
"""

import logging
from datetime import datetime
from typing import Any, Iterable, Optional

from app.db.mongodb import MongoDBManager
from app.models.safety import EmergencyReportEntry

logger = logging.getLogger(__name__)


class EmergencyReportRepository:
    """emergency_reports collection 的操作（append-only）。"""

    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        if collection is None:
            collection = MongoDBManager.get_emergency_reports_collection()
        # 頻率限制的兩種計數：同一位回報者、同一位病人，各自依時間往回數。
        await collection.create_index([("reporter_id", 1), ("reported_at", -1)])
        await collection.create_index([("patient_id", 1), ("reported_at", -1)])
        # expireAfterSeconds=0：以 expires_at 的時刻為準刪除。
        await collection.create_index("expires_at", expireAfterSeconds=0)

    @staticmethod
    async def append_many(
        entries: Iterable[EmergencyReportEntry], collection: Optional[Any] = None
    ) -> None:
        documents = [entry.model_dump() for entry in entries]
        if not documents:
            return
        if collection is None:
            collection = MongoDBManager.get_emergency_reports_collection()
        await collection.insert_many(documents)

    @staticmethod
    async def count_cross_person_sent(
        *,
        since: datetime,
        reporter_id: Optional[str] = None,
        patient_id: Optional[str] = None,
        collection: Optional[Any] = None,
    ) -> int:
        """替別人回報、且真的送到家人手上的次數。

        只數送達：沒送出去的通報沒有驚動任何人，不該吃掉額度。
        """
        if collection is None:
            collection = MongoDBManager.get_emergency_reports_collection()
        query: dict[str, Any] = {
            "cross_person": True,
            "outcome": "sent",
            "reported_at": {"$gte": since},
        }
        if reporter_id is not None:
            query["reporter_id"] = reporter_id
        if patient_id is not None:
            query["patient_id"] = patient_id
        return await collection.count_documents(query)
