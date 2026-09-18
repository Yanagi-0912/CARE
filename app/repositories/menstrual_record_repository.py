"""經期紀錄 (menstrual_records) 的資料庫操作。

PERSONAL 分類，只有本人可讀寫（app/models/family_authorization.py）；本檔案
只負責持久化，跨使用者的存取檢查在服務／路由層。
"""

from typing import Any, List, Optional

from bson import ObjectId
from datetime import date, datetime, timedelta, timezone

from app.db.mongodb import MongoDBManager
from app.models.health import MENSTRUAL_MAX_SPAN_DAYS, MenstrualRecord

# cycle_length_days／period_length_days 由服務層依前一筆紀錄現算後以
# model_copy 回填，不落地存進資料庫（見 MenstrualRecord 的欄位註解）。
_COMPUTED_FIELDS = {"cycle_length_days", "period_length_days"}

# 查詢單次回應至多 200 筆（constraints.md「狀態碼」，同血壓血糖量測查詢）。
MAX_LIST_RESULTS = 200


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
        """查詢本人全部經期紀錄，依開始日期新到舊排序，單次回應至多 200 筆
        （constraints.md「狀態碼」，同血壓血糖量測查詢）。"""
        if collection is None:
            collection = MongoDBManager.get_menstrual_records_collection()
        cursor = (
            collection.find({"user_id": user_id})
            .sort("start_date", -1)
            .limit(MAX_LIST_RESULTS)
        )
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

        兩邊「沒有 end_date（仍在進行中）」都不是沒有上界，而是視為佔滿
        ``[start, start + MENSTRUAL_MAX_SPAN_DAYS]``——這是輸入驗證允許的
        最長合法經期天數（見 ``app/models/health.py`` 的
        ``MENSTRUAL_MAX_SPAN_DAYS``）：一筆仍在進行中的經期至少擋得住「新
        開始日期落在其中」，但一筆上個月忘記填結束日期的舊紀錄，不該因為
        「沒有上界」就永遠擋住這個月的新紀錄。

        日期以 ``YYYY-MM-DD`` 字串儲存，Mongo 無法直接對字串做日期算術，
        因此這裡只用 ``user_id``（與 ``exclude_id``）向資料庫取回這位使用者
        的候選紀錄，精確的區間相交判定在應用層以 ``datetime.date`` 進行——
        一位使用者的經期紀錄量不大，全部取回逐筆比對的成本可忽略。

        ``exclude_id`` 給更新既有紀錄時用（更新後的日期不該跟自己比對）。
        """
        if collection is None:
            collection = MongoDBManager.get_menstrual_records_collection()
        query: dict = {"user_id": user_id}
        if exclude_id is not None:
            query["_id"] = {"$ne": exclude_id}
        # Task 9 修復：同 ``list_by_user`` 一樣加上單次回應上限——這是本次
        # change 裡唯一一個沒有加上限的查詢，`to_list(length=None)` 原本會
        # 無上限撈出這位使用者的全部候選紀錄。
        cursor = collection.find(query).limit(MAX_LIST_RESULTS)
        docs = await cursor.to_list(length=None)
        candidates = [
            MenstrualRecord(**{**doc, "_id": str(doc["_id"])}) for doc in docs
        ]

        new_start = date.fromisoformat(start_date)
        new_end = (
            date.fromisoformat(end_date)
            if end_date
            else new_start + timedelta(days=MENSTRUAL_MAX_SPAN_DAYS)
        )

        def _effective_end(record: MenstrualRecord) -> date:
            if record.end_date:
                return date.fromisoformat(record.end_date)
            return date.fromisoformat(record.start_date) + timedelta(
                days=MENSTRUAL_MAX_SPAN_DAYS
            )

        return [
            record
            for record in candidates
            if date.fromisoformat(record.start_date) <= new_end
            and new_start <= _effective_end(record)
        ]
