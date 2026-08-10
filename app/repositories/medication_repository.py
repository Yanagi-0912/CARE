import logging
from datetime import datetime, timezone
from typing import Any, List, Optional
from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db.mongodb import MongoDBManager
from app.models.medication import (
    TAIPEI_TZ,
    Medication,
    MedicationLog,
    MedicationReminder,
)

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


def _is_schedulable(doc: dict, today: str) -> bool:
    """判斷一筆規則「現在」是不是排程器（`list_active_reminders_up_to_time`）
    真的會挑中的那種——啟用中，且今天落在 start_date～end_date 區間內
    （欄位缺席或 end_date 為 null 視為不限，語意對齊 `_active_date_window`）。

    `enabled` 欄位缺席時視為「不可排程」（`doc.get("enabled", False)`），
    而不是預設為 True：`list_active_reminders_up_to_time` 的查詢條件是
    exact match `{"enabled": True}`，缺這個欄位的文件不會被該查詢挑中。
    這裡若把缺席當成可排程，就會出現「這個判斷說沒問題、但排程器永遠不會
    推播」的文件——判斷通過代表呼叫端不會去修它，於是它就永遠卡在
    「看起來正常、實際上不會推播」的狀態，正是這整個修正要避免的悄悄失效。
    嚴格的一邊（查詢）沒有錯的空間可以退讓，所以讓判斷跟著查詢收斂到
    同一個答案，而不是反過來。
    """
    if not doc.get("enabled", False):
        return False
    start_date = doc.get("start_date")
    if start_date and start_date > today:
        return False
    end_date = doc.get("end_date")
    if end_date and end_date < today:
        return False
    return True


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
    async def find_or_create_reminder(
        user_id: str,
        slot_type: str,
        creator_user_id: str,
        scheduled_time: str,
        collection: Optional[Any] = None,
    ) -> tuple[MedicationReminder, bool]:
        """取得或建立某位使用者在某個時段「排程器實際會推播」的提醒規則。

        回傳 `(reminder, reactivated)`。`reactivated` 為 True 代表命中的既有
        規則原本不會被排程器挑中（停用、療程已過期、或還沒到 start_date），
        這次呼叫把它改回可排程狀態——呼叫端要能把這件事告知使用者，而不是
        悄悄復活一筆他當初主動關掉、或早已結束療程的規則。

        查詢條件只看 `{user_id, slot_type}`，不篩 enabled 或日期區間——
        同一位使用者的同一個時段永遠只該有一份規則，不論它現在是開是關、
        療程有沒有過期。這是本方法要維持的核心不變量：「一個時段一份
        document」，讓排程器（`list_active_reminders_up_to_time`）與唯一索引
        `(reminder_id, scheduled_at)` 都不可能因為同一個時段存在兩份規則而
        讓使用者收到兩則同一時段的推播。

        （先前版本的查詢條件額外加了 enabled=True 與日期區間限制，理由是
        「不能悄悄復活使用者主動關掉的規則」；但那個限制的後果是查不到
        「活著」的規則時會走 upsert 插入第二筆，同一個時段從此有兩份
        document，兩份都可能同時被排程器挑中——這正是本方法現在要避免的
        重複推播問題本身，比「連帶恢復其他藥」更嚴重。改回只看
        `{user_id, slot_type}` 之後，「復活規則會不會連帶恢復其他藥」改由
        `reactivated` 這個回傳值解決：呼叫端據此在使用者確認前先揭露、
        提交後再於結果中如實告知，而不是用「乾脆不要復活、另開一筆」來
        迴避揭露。）

        找到既有規則但目前不可排程時，把它改成可排程：`enabled` 設回
        True、清空已過期的 `end_date`、把還沒到的 `start_date` 拉回今天。
        既有規則本身可排程時完全不碰它——不執行第二次寫入，沿用它原本的
        排程時間等設定，不能因為又有人提交同一個時段的藥就悄悄覆蓋使用者
        已經調整過的設定。

        MongoDB 保證的是「單一這次呼叫」對它命中的那份文件而言是原子
        操作——不會有另一個寫入插在它的讀取與寫入之間。但這不等於「兩個
        並行呼叫只會有一個真的插入」：`{user_id, slot_type}` 上沒有 unique
        index，兩個呼叫若都同時判斷「這個時段還沒有任何規則」，MongoDB
        並不保證只有一邊的 upsert 會成功，兩邊都可能各自插入一筆。這裡
        刻意不建那個 unique index——舊資料可能已經存在同一位使用者、同一
        時段的重複規則，建立唯一索引會直接讓應用起不來。也就是說，本方法
        把「同一個時段從零份變成兩份」的機率降到很低（只發生在這個時段
        真的一份規則都沒有、且兩個提交幾乎同時競爭的極端情況），但沒有把
        它降到零；真正的重複提交防護在 `mark_committed` 的提交權 CAS，
        不是這裡。至於「這個時段已經有規則」的情況（不論它現在是開是關），
        本方法保證後續呼叫一律命中同一份文件，不會再繼續增生。

        `$setOnInsert` 只在真的建立新文件時套用；`today` 只算一次並傳給
        兩個階段共用，避免兩次呼叫 `_today_date_str()` 在極端情況下跨過
        午夜而算出不一致的結果。
        """
        if collection is None:
            collection = MongoDBManager.get_medication_reminders_collection()
        now = datetime.now(timezone.utc)
        today = _today_date_str()
        query = {"user_id": user_id, "slot_type": slot_type}
        document = await collection.find_one_and_update(
            query,
            {
                "$setOnInsert": {
                    "_id": str(ObjectId()),
                    "creator_user_id": creator_user_id,
                    "scheduled_time": scheduled_time,
                    "start_date": today,
                    "end_date": None,
                    "enabled": True,
                    "medication_ids": [],
                    "created_at": now,
                    "updated_at": now,
                }
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

        reactivated = False
        if not _is_schedulable(document, today):
            fix: dict = {}
            # 預設值必須跟 _is_schedulable 一致（缺欄位視為不可排程）：
            # 若這裡仍用 True 當預設，缺 enabled 欄位的舊資料會被判斷為
            # 「不可排程」卻因為這一行覺得它「本來就是 enabled」而不產生
            # 任何修補，最終 fix 是空字典、不會寫入、reactivated 仍是
            # False——判斷對了，卻沒有真的把文件修好，等於白判斷。
            if not document.get("enabled", False):
                fix["enabled"] = True
            end_date = document.get("end_date")
            if end_date and end_date < today:
                fix["end_date"] = None
            start_date = document.get("start_date")
            if start_date and start_date > today:
                fix["start_date"] = today
            if fix:
                fix["updated_at"] = now
                document = await collection.find_one_and_update(
                    {"_id": document["_id"]},
                    {"$set": fix},
                    return_document=ReturnDocument.AFTER,
                )
                reactivated = True

        document["_id"] = str(document["_id"])
        return MedicationReminder(**document), reactivated

    @staticmethod
    async def get_reminder_by_id(reminder_id: str) -> Optional[MedicationReminder]:
        col = MongoDBManager.get_medication_reminders_collection()
        doc = await col.find_one({"_id": reminder_id})
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return MedicationReminder(**doc)

    @staticmethod
    async def find_by_ids(
        reminder_ids: List[str], collection: Optional[Any] = None
    ) -> List[MedicationReminder]:
        """批次查詢多筆規則。

        推播組裝文案時（見 `MedicationScheduler._TickMedicationNameCache`）用來把
        「查規則」從逐筆改成整批——同一個時段常常有多位使用者共用，一個 tick 內
        對同一批 log 各自查一次規則會是 O(log 數) 次序列往返，改成 `$in` 一次查完
        整批 reminder_id 就是固定 1 次。
        """
        if not reminder_ids:
            return []
        if collection is None:
            collection = MongoDBManager.get_medication_reminders_collection()
        cursor = collection.find({"_id": {"$in": reminder_ids}})
        docs = await cursor.to_list(length=None)
        return [MedicationReminder(**{**doc, "_id": str(doc["_id"])}) for doc in docs]

    @staticmethod
    async def list_reminders_by_user(
        user_id: str, collection: Optional[Any] = None
    ) -> List[MedicationReminder]:
        # collection 可注入：沿用本檔案其他新方法（find_or_create_reminder／
        # link_medications_to_reminder）與 MedicationRepository 一貫的慣例，
        # 測試才能直接餵假的 collection，不需要 monkeypatch 掉整個 staticmethod。
        col = collection if collection is not None else MongoDBManager.get_medication_reminders_collection()
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

    @staticmethod
    async def link_medications_to_reminder(
        reminder_id: str,
        medication_ids: List[str],
        collection: Optional[Any] = None,
    ) -> bool:
        """把藥品掛到既有的時段規則上。

        用 $addToSet 而非 $push：同一份處方被重複提交、或使用者把同一種藥
        再指定一次到同一個時段時，重複的 id 會讓推播把同一種藥列兩遍。
        """
        if not medication_ids:
            return False
        if collection is None:
            collection = MongoDBManager.get_medication_reminders_collection()
        result = await collection.update_one(
            {"_id": reminder_id},
            {
                "$addToSet": {"medication_ids": {"$each": medication_ids}},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
        )
        return result.matched_count > 0


class MedicationRepository:
    """
    藥品 (medications) 資料庫操作。

    與時段規則分開存放，因此可以單獨停用或結束某一種藥的療程，
    而不動到同一時段的其他藥。
    """

    @staticmethod
    async def create_many(
        medications: List[Medication], collection: Optional[Any] = None
    ) -> List[Medication]:
        if not medications:
            return []
        if collection is None:
            collection = MongoDBManager.get_medications_collection()

        documents = []
        created = []
        for medication in medications:
            document = medication.model_dump(by_alias=True)
            if not document.get("_id"):
                document["_id"] = str(ObjectId())
            documents.append(document)
            created.append(medication.model_copy(update={"id": document["_id"]}))

        await collection.insert_many(documents)
        return created

    @staticmethod
    async def find_by_ids(
        medication_ids: List[str], collection: Optional[Any] = None
    ) -> List[Medication]:
        if not medication_ids:
            return []
        if collection is None:
            collection = MongoDBManager.get_medications_collection()
        cursor = collection.find({"_id": {"$in": medication_ids}})
        docs = await cursor.to_list(length=None)
        return [Medication(**{**doc, "_id": str(doc["_id"])}) for doc in docs]

    @staticmethod
    async def find_active_by_ids(
        medication_ids: List[str],
        date_str: str,
        collection: Optional[Any] = None,
    ) -> List[Medication]:
        """只回傳當日仍有效的藥品。

        推播的藥品清單走這裡：已停用或療程已結束的藥不該再出現在提醒上，
        但它們的失效不影響時段規則本身是否推播。
        """
        if not medication_ids:
            return []
        if collection is None:
            collection = MongoDBManager.get_medications_collection()
        query = {
            "_id": {"$in": medication_ids},
            "enabled": True,
            "$and": _active_date_window(date_str),
        }
        cursor = collection.find(query)
        docs = await cursor.to_list(length=None)
        return [Medication(**{**doc, "_id": str(doc["_id"])}) for doc in docs]

    @staticmethod
    async def set_enabled(
        medication_id: str,
        user_id: str,
        enabled: bool,
        collection: Optional[Any] = None,
    ) -> bool:
        if collection is None:
            collection = MongoDBManager.get_medications_collection()
        result = await collection.update_one(
            {"_id": medication_id, "user_id": user_id},
            {"$set": {"enabled": enabled, "updated_at": datetime.now(timezone.utc)}},
        )
        return result.matched_count > 0


class MedicationLogRepository:
    """
    用藥執行與催促警報日誌 (medication_logs) 資料庫操作
    每個定時用藥觸發僅維護單一 Document，後續定時任務狀態與使用者操作皆更新此單一 Document
    """

    @staticmethod
    async def ensure_indexes() -> None:
        """
        建立 (reminder_id, scheduled_at) 唯一索引。

        `upsert_log` 的 `$setOnInsert` 只保證「同一個 filter 條件下不覆蓋既有欄位」，
        不保證併發時只插入一筆——沒有唯一索引時，兩個實例同時 upsert 會各插入一份
        document，於是同一個時段有兩筆 log、各自被搶佔、各自推播，推播權搶佔就形同虛設。
        """
        col = MongoDBManager.get_medication_logs_collection()
        try:
            await col.create_index(
                [("reminder_id", 1), ("scheduled_at", 1)],
                unique=True,
                name="uniq_reminder_scheduled",
            )
        except Exception:
            # 既有資料若已存在重複組合，建索引會失敗。這時不該讓整個 app 起不來，
            # 但必須讓維運看得到——重複的 log 會造成重複推播。
            logger.exception(
                "[MedicationLogRepository] 無法建立 medication_logs 唯一索引；"
                "請先清除 (reminder_id, scheduled_at) 重複的紀錄，否則多實例並存時會重複推播"
            )

    @staticmethod
    async def upsert_log(log: MedicationLog) -> tuple[MedicationLog, bool]:
        """
        以 (reminder_id, scheduled_at) 為唯一識別進行 upsert。
        初始建立為 1 筆 Document；後續的點擊/定時更新均直接變更此單一 Document。

        回傳 `(log, created)`。`created` 為 True 代表這筆是本次呼叫才新建的——排程器
        靠它判斷「這個錯過的時段是不是這次才發現的」，避免每個 tick 重複通知家屬。
        """
        col = MongoDBManager.get_medication_logs_collection()
        filter_query = {
            "reminder_id": log.reminder_id,
            "scheduled_at": log.scheduled_at,
        }
        set_on_insert = log.model_dump(by_alias=True, exclude_none=True)
        if "_id" not in set_on_insert or not set_on_insert["_id"]:
            set_on_insert["_id"] = str(ObjectId())

        created = False
        try:
            result = await col.update_one(
                filter_query,
                {"$setOnInsert": set_on_insert},
                upsert=True,
            )
            created = result.upserted_id is not None
        except DuplicateKeyError:
            # 併發時另一個實例先插入了，唯一索引擋下這次插入。
            # 對呼叫端而言等同「這筆已經存在」，不是本次建立。
            created = False

        doc = await col.find_one(filter_query)
        doc["_id"] = str(doc["_id"])
        return MedicationLog(**doc), created

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


    # ── 推播權搶佔 ────────────────────────────────────────────────────
    #
    # 「查詢待推播 → 呼叫 LINE API → 標記已送出」這三步之間沒有原子性。只要同時有
    # 兩個 backend 實例在跑排程，兩邊就會查到同一筆未送出的 log（因為第一個實例
    # 還沒來得及標記），各推一次，使用者收到兩則相同提醒。
    #
    # 這不是假設性的情境：Helm 的 backend deployment 是 maxUnavailable=0 + maxSurge=1，
    # 意思是舊 pod 必須等新 pod Ready 之後才被終止——每次滾動更新都保證有一段
    # 新舊並存的時間，而排程器在 lifespan startup 就啟動、第一件事就是跑一次 tick。
    # 本機 uvicorn 連上同一個資料庫時更是持續並存。
    #
    # 因此推播前先以「旗標仍為 False」為條件做單一 document 的原子更新搶下推播權
    # （MongoDB 保證單一 document 的更新是原子的），搶輸的實例直接跳過；推播失敗
    # 再把旗標還原，交給下一個 tick 重試。

    @staticmethod
    async def claim_patient_reminder(log_id: str) -> bool:
        """搶下 T+0min 首刷提醒的推播權，回傳 True 代表本實例取得推播權。"""
        col = MongoDBManager.get_medication_logs_collection()
        result = await col.update_one(
            {"_id": log_id, "patient_reminder_sent": False},
            {"$set": {"patient_reminder_sent": True}},
        )
        return result.modified_count > 0

    @staticmethod
    async def release_patient_reminder(log_id: str) -> bool:
        """推播失敗時還原 T+0min 首刷提醒的旗標，讓下一個 tick 重試。"""
        col = MongoDBManager.get_medication_logs_collection()
        result = await col.update_one(
            {"_id": log_id, "patient_reminder_sent": True},
            {"$set": {"patient_reminder_sent": False}},
        )
        return result.modified_count > 0

    @staticmethod
    async def claim_patient_urgent_reminder(log_id: str) -> bool:
        """搶下 T+20min 催促提醒的推播權。"""
        col = MongoDBManager.get_medication_logs_collection()
        result = await col.update_one(
            {"_id": log_id, "urgent_reminder_sent": False},
            {"$set": {"urgent_reminder_sent": True}},
        )
        return result.modified_count > 0

    @staticmethod
    async def release_patient_urgent_reminder(log_id: str) -> bool:
        """推播失敗時還原 T+20min 催促提醒的旗標。"""
        col = MongoDBManager.get_medication_logs_collection()
        result = await col.update_one(
            {"_id": log_id, "urgent_reminder_sent": True},
            {"$set": {"urgent_reminder_sent": False}},
        )
        return result.modified_count > 0

    @staticmethod
    async def claim_caregiver_alert(log_id: str) -> bool:
        """搶下 T+30min 家屬警報的推播權，同時把狀態設為 missed。"""
        col = MongoDBManager.get_medication_logs_collection()
        result = await col.update_one(
            {"_id": log_id, "caregiver_alert_sent": False},
            {"$set": {"caregiver_alert_sent": True, "status": "missed"}},
        )
        return result.modified_count > 0

    @staticmethod
    async def release_caregiver_alert(log_id: str) -> bool:
        """
        推播失敗時還原 T+30min 家屬警報的旗標。

        status 只在仍為 missed 時才回寫 pending：使用者可能在推播失敗的空檔按下
        「已用藥」，那時 status 是 taken，不能被還原動作蓋回去。
        """
        col = MongoDBManager.get_medication_logs_collection()
        result = await col.update_one(
            {"_id": log_id, "caregiver_alert_sent": True, "status": "missed"},
            {"$set": {"caregiver_alert_sent": False, "status": "pending"}},
        )
        return result.modified_count > 0

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
