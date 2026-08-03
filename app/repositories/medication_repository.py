import logging
from datetime import datetime, timezone
from typing import List, Optional
from bson import ObjectId

from app.db.mongodb import MongoDBManager
from app.models.medication import TAIPEI_TZ, MedicationLog, MedicationReminder

logger = logging.getLogger(__name__)


def _today_date_str() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


def _active_date_window(date_str: str) -> List[dict]:
    """
    日期區間條件：start_date <= date_str <= end_date。
    欄位不存在、或 end_date 為 null（長期提醒）皆視為不限。
    """
    return [
        {"$or": [{"start_date": {"$exists": False}}, {"start_date": {"$lte": date_str}}]},
        {
            "$or": [
                {"end_date": None},
                {"end_date": {"$exists": False}},
                {"end_date": {"$gte": date_str}},
            ]
        },
    ]


class MedicationReminderRepository:
    """
    用藥提醒 (medication_reminders) 資料庫操作
    """

    @staticmethod
    async def create_reminder(reminder: MedicationReminder) -> MedicationReminder:
        col = MongoDBManager.get_medication_reminders_collection()
        doc = reminder.model_dump(by_alias=True, exclude_none=True)
        if "_id" not in doc or not doc["_id"]:
            doc["_id"] = str(ObjectId())
        await col.insert_one(doc)
        doc["_id"] = str(doc["_id"])
        return MedicationReminder(**doc)

    @staticmethod
    async def get_reminder_by_id(reminder_id: str) -> Optional[MedicationReminder]:
        col = MongoDBManager.get_medication_reminders_collection()
        doc = await col.find_one({"_id": reminder_id})
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return MedicationReminder(**doc)

    @staticmethod
    async def list_reminders_by_user(user_id: str) -> List[MedicationReminder]:
        col = MongoDBManager.get_medication_reminders_collection()
        cursor = col.find({"user_id": user_id})
        docs = await cursor.to_list(length=None)
        reminders = []
        for doc in docs:
            doc["_id"] = str(doc["_id"])
            reminders.append(MedicationReminder(**doc))
        return reminders

    @staticmethod
    async def list_reminders_by_creator(creator_user_id: str) -> List[MedicationReminder]:
        col = MongoDBManager.get_medication_reminders_collection()
        cursor = col.find({"creator_user_id": creator_user_id})
        docs = await cursor.to_list(length=None)
        reminders = []
        for doc in docs:
            doc["_id"] = str(doc["_id"])
            reminders.append(MedicationReminder(**doc))
        return reminders

    @staticmethod
    async def list_active_reminders_up_to_time(
        max_scheduled_time: str, target_date_str: Optional[str] = None
    ) -> List[MedicationReminder]:
        """
        查詢當日已到達排程時間 (scheduled_time <= max_scheduled_time) 且為啟用狀態的提醒規則
        """
        col = MongoDBManager.get_medication_reminders_collection()
        date_str = target_date_str or _today_date_str()
        query = {
            "scheduled_time": {"$lte": max_scheduled_time},
            "enabled": True,
            "$and": _active_date_window(date_str),
        }
        cursor = col.find(query)
        docs = await cursor.to_list(length=None)
        reminders = []
        for doc in docs:
            doc["_id"] = str(doc["_id"])
            reminders.append(MedicationReminder(**doc))
        return reminders

    @staticmethod
    async def update_reminder(reminder_id: str, update_data: dict) -> Optional[MedicationReminder]:
        col = MongoDBManager.get_medication_reminders_collection()
        now = datetime.now(tz=timezone.utc)
        update_doc = {k: v for k, v in update_data.items() if v is not None}
        update_doc["updated_at"] = now

        result = await col.update_one({"_id": reminder_id}, {"$set": update_doc})
        if result.matched_count == 0:
            return None
        return await MedicationReminderRepository.get_reminder_by_id(reminder_id)

    @staticmethod
    async def delete_reminder(reminder_id: str) -> bool:
        col = MongoDBManager.get_medication_reminders_collection()
        result = await col.delete_one({"_id": reminder_id})
        return result.deleted_count > 0


class MedicationLogRepository:
    """
    用藥執行與催促警報日誌 (medication_logs) 資料庫操作
    每個定時用藥觸發僅維護單一 Document，後續定時任務狀態與使用者操作皆更新此單一 Document
    """

    @staticmethod
    async def upsert_log(log: MedicationLog) -> MedicationLog:
        """
        以 (reminder_id, scheduled_at) 為唯一識別進行 upsert。
        初始建立為 1 筆 Document；後續的點擊/定時更新均直接變更此單一 Document。
        """
        col = MongoDBManager.get_medication_logs_collection()
        filter_query = {
            "reminder_id": log.reminder_id,
            "scheduled_at": log.scheduled_at,
        }
        set_on_insert = log.model_dump(by_alias=True, exclude_none=True)
        if "_id" not in set_on_insert or not set_on_insert["_id"]:
            set_on_insert["_id"] = str(ObjectId())

        await col.update_one(
            filter_query,
            {"$setOnInsert": set_on_insert},
            upsert=True,
        )
        doc = await col.find_one(filter_query)
        doc["_id"] = str(doc["_id"])
        return MedicationLog(**doc)

    @staticmethod
    async def get_log_by_id(log_id: str) -> Optional[MedicationLog]:
        col = MongoDBManager.get_medication_logs_collection()
        doc = await col.find_one({"_id": log_id})
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return MedicationLog(**doc)

    @staticmethod
    async def mark_as_taken(log_id: str, taken_at: Optional[datetime] = None) -> Optional[MedicationLog]:
        """更新單一 Document 狀態為已服藥 (允許 pending 或 missed 狀態被標記為 taken)"""
        col = MongoDBManager.get_medication_logs_collection()
        now = taken_at or datetime.now(tz=timezone.utc)
        result = await col.update_one(
            {"_id": log_id, "status": {"$in": ["pending", "missed"]}},
            {"$set": {"status": "taken", "taken_at": now}},
        )
        if result.matched_count == 0:
            log = await MedicationLogRepository.get_log_by_id(log_id)
            if log and log.status == "taken":
                return log
            return None
        return await MedicationLogRepository.get_log_by_id(log_id)


    @staticmethod
    async def mark_patient_reminder_sent(log_id: str) -> bool:
        """更新單一 Document 標記 T+0min 首刷提醒已發送"""
        col = MongoDBManager.get_medication_logs_collection()
        result = await col.update_one(
            {"_id": log_id},
            {"$set": {"patient_reminder_sent": True}},
        )
        return result.matched_count > 0

    @staticmethod
    async def mark_patient_urgent_reminder_sent(log_id: str) -> bool:
        """更新單一 Document 標記 T+20min 催促提醒已發送"""
        col = MongoDBManager.get_medication_logs_collection()
        result = await col.update_one(
            {"_id": log_id},
            {"$set": {"urgent_reminder_sent": True}},
        )
        return result.matched_count > 0

    @staticmethod
    async def mark_caregiver_alert_sent(log_id: str) -> bool:
        """更新單一 Document 標記 T+30min 警報已發送且狀態設為 missed"""
        col = MongoDBManager.get_medication_logs_collection()
        result = await col.update_one(
            {"_id": log_id},
            {"$set": {"caregiver_alert_sent": True, "status": "missed"}},
        )
        return result.matched_count > 0

    @staticmethod
    async def list_pending_patient_reminders(threshold_time: datetime) -> List[MedicationLog]:
        col = MongoDBManager.get_medication_logs_collection()
        query = {
            "status": "pending",
            "patient_reminder_sent": False,
            "scheduled_at": {"$lte": threshold_time},
        }
        cursor = col.find(query)
        docs = await cursor.to_list(length=None)
        return [MedicationLog(**{**doc, "_id": str(doc["_id"])}) for doc in docs]

    @staticmethod
    async def list_pending_urgent_reminders(threshold_time: datetime) -> List[MedicationLog]:
        """
        查詢已過 T+20min (scheduled_at <= threshold_time) 且狀態仍為 pending 尚未發送催促提醒的日誌。
        使用 $lte 可防範排程檢查秒數偏差或伺服器重啟造成的延遲漏發。
        """
        col = MongoDBManager.get_medication_logs_collection()
        query = {
            "status": "pending",
            "patient_reminder_sent": True,
            "urgent_reminder_sent": False,
            "scheduled_at": {"$lte": threshold_time},
        }
        cursor = col.find(query)
        docs = await cursor.to_list(length=None)
        return [MedicationLog(**{**doc, "_id": str(doc["_id"])}) for doc in docs]

    @staticmethod
    async def list_pending_caregiver_alerts(threshold_time: datetime) -> List[MedicationLog]:
        """
        查詢已過 T+30min (timeout_at <= threshold_time) 且狀態仍為 pending 尚未發送家屬警報的日誌。
        使用 $lte 可防範排程檢查秒數偏差或伺服器重啟造成的延遲漏發。
        """
        col = MongoDBManager.get_medication_logs_collection()
        query = {
            "status": "pending",
            "caregiver_alert_sent": False,
            "timeout_at": {"$lte": threshold_time},
        }
        cursor = col.find(query)
        docs = await cursor.to_list(length=None)
        return [MedicationLog(**{**doc, "_id": str(doc["_id"])}) for doc in docs]



    @staticmethod
    async def list_logs_by_user(user_id: str, limit: int = 50) -> List[MedicationLog]:
        col = MongoDBManager.get_medication_logs_collection()
        cursor = col.find({"user_id": user_id}).sort("scheduled_at", -1).limit(limit)
        docs = await cursor.to_list(length=None)
        return [MedicationLog(**{**doc, "_id": str(doc["_id"])}) for doc in docs]
