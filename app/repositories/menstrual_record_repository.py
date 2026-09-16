"""經期紀錄 (menstrual_records) 的資料庫操作。

PERSONAL 分類，只有本人可讀寫（app/models/family_authorization.py）；本檔案
只負責持久化，跨使用者的存取檢查在服務／路由層。
"""

from typing import Any, List, Optional

from bson import ObjectId
from datetime import datetime, timezone

from app.db.mongodb import MongoDBManager
from app.models.health import MenstrualRecord

# cycle_length_days／period_length_days 由服務層依前一筆紀錄現算後以
# model_copy 回填，不落地存進資料庫（見 MenstrualRecord 的欄位註解）。
_COMPUTED_FIELDS = {"cycle_length_days", "period_length_days"}


class MenstrualRecordRepository:
    """``menstrual_records`` collection 的操作。"""

    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        if collection is None:
            collection = MongoDBManager.get_menstrual_records_collection()
        await collection.create_index([("user_id", 1), ("start_date", -1)])

    @staticmethod
    async def add(
        record: MenstrualRecord, collection: Optional[Any] = None
    ) -> MenstrualRecord:
        if collection is None:
            collection = MongoDBManager.get_menstrual_records_collection()
        doc = record.model_dump(
            by_alias=True, exclude=_COMPUTED_FIELDS, exclude_none=True
        )
        if not doc.get("_id"):
            doc["_id"] = str(ObjectId())
        await collection.insert_one(doc)
        doc["_id"] = str(doc["_id"])
        return MenstrualRecord(**doc)

    @staticmethod
    async def list_by_user(
        user_id: str, collection: Optional[Any] = None
    ) -> List[MenstrualRecord]:
        if collection is None:
            collection = MongoDBManager.get_menstrual_records_collection()
        cursor = collection.find({"user_id": user_id}).sort("start_date", -1)
        docs = await cursor.to_list(length=None)
        return [MenstrualRecord(**{**doc, "_id": str(doc["_id"])}) for doc in docs]

    @staticmethod
    async def get_by_id(
        record_id: str, collection: Optional[Any] = None
    ) -> Optional[MenstrualRecord]:
        if collection is None:
            collection = MongoDBManager.get_menstrual_records_collection()
        doc = await collection.find_one({"_id": record_id})
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return MenstrualRecord(**doc)

    @staticmethod
    async def update(
        record_id: str, update_data: dict, collection: Optional[Any] = None
    ) -> Optional[MenstrualRecord]:
        """本人事後補上結束日期或修正紀錄（menstrual-cycle-log spec「記錄
        經期」）。與 MedicationReminderRepository.update_reminder 同慣例：
        收到什麼就寫什麼，不過濾 None——清空一個欄位由呼叫端決定，這裡不
        重複做那個判斷。
        """
        if collection is None:
            collection = MongoDBManager.get_menstrual_records_collection()
        update_doc = dict(update_data)
        update_doc["updated_at"] = datetime.now(timezone.utc)
        result = await collection.update_one({"_id": record_id}, {"$set": update_doc})
        if result.matched_count == 0:
            return None
        doc = await collection.find_one({"_id": record_id})
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return MenstrualRecord(**doc)

    @staticmethod
    async def delete(record_id: str, collection: Optional[Any] = None) -> bool:
        if collection is None:
            collection = MongoDBManager.get_menstrual_records_collection()
        result = await collection.delete_one({"_id": record_id})
        return result.deleted_count > 0

    @staticmethod
    async def find_overlapping(
        user_id: str,
        start_date: str,
        end_date: Optional[str] = None,
        exclude_id: Optional[str] = None,
        collection: Optional[Any] = None,
    ) -> List[MenstrualRecord]:
        """重疊查詢（menstrual-cycle-log spec「記錄經期」：「同一使用者的
        經期 SHALL NOT 重疊」）。

        標準區間重疊公式：[existing.start, existing.end] 與
        [start_date, upper_bound] 相交，當且僅當
        ``existing.start <= upper_bound`` 且 ``start_date <= existing.end``。
        既有紀錄沒有 ``end_date``（仍在進行中）視為沒有上界，任何新開始日期
        都算重疊。``upper_bound`` 在新紀錄本身沒有 ``end_date`` 時退回
        ``start_date``——這已經涵蓋 spec 指名的情境（新開始日期落在既有經期
        的起訖之間），新紀錄自己也帶 end_date 時則是完整的雙區間相交判定。

        ``exclude_id`` 給更新既有紀錄時用（更新後的日期不該跟自己比對）。
        """
        if collection is None:
            collection = MongoDBManager.get_menstrual_records_collection()
        upper_bound = end_date or start_date
        query: dict = {
            "user_id": user_id,
            "start_date": {"$lte": upper_bound},
            "$or": [
                {"end_date": None},
                {"end_date": {"$gte": start_date}},
            ],
        }
        if exclude_id is not None:
            query["_id"] = {"$ne": exclude_id}
        cursor = collection.find(query)
        docs = await cursor.to_list(length=None)
        return [MenstrualRecord(**{**doc, "_id": str(doc["_id"])}) for doc in docs]
