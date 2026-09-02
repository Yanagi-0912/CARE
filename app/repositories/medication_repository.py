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

# 單一階段的推播嘗試次數上限（含第一次）。超過就放棄，不再把推播權還回去。
#
# 為什麼要有上限：`release_*` 把旗標回寫成「未送出」是為了讓下一個 tick 重試，
# 這對瞬時故障（資料庫瞬斷、LINE 端 5xx）是對的。但有一整類錯誤不會自行恢復
# ——最典型的是 LINE 月推播額度耗盡的 429——在那種情況下「還回去、下一輪再試」
# 會變成每 60 秒重試一次、直到月底都不會停，而且每一輪都會重新查 profile、
# 重組 Flex、再打一次注定失敗的 API。
#
# 取 5：涵蓋約 5 分鐘的瞬時故障（tick 間隔 60 秒），仍遠小於 T+20 催促的門檻，
# 因此一個階段耗盡預算不會延後或吃掉下一個階段的推播時機。
MAX_PUSH_ATTEMPTS = 5


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
        # 收到什麼就寫什麼，不再過濾 None。過濾 None 是「清空 end_date」失效的
        # 第二道濾網（第一道在 MedicationService.update_reminder 的 model_dump），
        # 只修其中一層完全沒有效果。哪些欄位允許 null 由服務層界定並在那裡擋成
        # 400，這一層不重複做那個判斷——分散在兩處只會讓兩邊都以為對方有擋。
        update_doc = dict(update_data)
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
        """更新單一 Document 狀態為已服藥。

        `pending`、`missed`、`cancelled` 三種狀態都允許轉成 `taken`：使用者按下的
        確認一律優先於系統推得的狀態。`missed` 是排程器判定的逾時，`cancelled` 是
        規則被關閉時的註銷——兩者都可能發生在使用者其實已經服藥之後（先吃了藥，
        才進 LIFF 關掉這個時段，最後才想起來按推播訊息上還留著的【我已用藥】）。
        真的吃過藥是事實，紀錄應該收斂成 `taken`。

        放寬狀態條件不會讓已經停下的推播復活：三階推播的待推播查詢限定
        `status="pending"`，`taken` 同樣挑不到。
        """
        col = MongoDBManager.get_medication_logs_collection()
        now = taken_at or datetime.now(tz=timezone.utc)
        result = await col.update_one(
            {"_id": log_id, "status": {"$in": ["pending", "missed", "cancelled"]}},
            {"$set": {"status": "taken", "taken_at": now}},
        )
        if result.matched_count == 0:
            log = await MedicationLogRepository.get_log_by_id(log_id)
            if log and log.status == "taken":
                return log
            return None
        return await MedicationLogRepository.get_log_by_id(log_id)

    @staticmethod
    async def cancel_pending_by_reminder(
        reminder_id: str,
        collection: Optional[Any] = None,
    ) -> int:
        """把某筆時段規則底下還沒確認的紀錄改為 cancelled，回傳受影響的筆數。

        使用者關閉時段規則時呼叫。三個推播階段（`list_pending_patient_reminders`／
        `list_pending_urgent_reminders`／`list_pending_caregiver_alerts`）的查詢
        條件都帶 `status="pending"`，所以狀態一離開 pending，那些查詢就再也挑不到
        這筆紀錄——後續的催促與家屬逾時警報自然停下，不需要在推播路徑上多做一次
        規則的 join（那條路徑的原子搶佔行為已有既定保證，不動它）。

        條件限定 `status="pending"`，不是只用 reminder_id：
        - 已 `taken` 的不能改——那是使用者真的吃過藥的事實。
        - 已 `missed` 的也不改——家屬警報早就送出去了，事後把它變成「不算漏吃」
          會讓資料庫與已經送到家屬手上的通知互相矛盾。

        沒有帶日期條件。理由：紀錄是惰性展開的，`pending` 只會是還在三階推播時間
        窗內的那一筆——更早的都已被 T+30 階段改成 `missed`，未來的還沒展開。多加
        一個日期範圍條件只會讓「關閉之後仍殘留 pending」多一種可能。
        """
        if collection is None:
            collection = MongoDBManager.get_medication_logs_collection()
        result = await collection.update_many(
            {"reminder_id": reminder_id, "status": "pending"},
            {"$set": {"status": "cancelled"}},
        )
        return result.modified_count

    @staticmethod
    async def cancel_pending_by_reminder_ids(
        reminder_ids: List[str],
        scheduled_from: Optional[datetime] = None,
        collection: Optional[Any] = None,
    ) -> int:
        """`cancel_pending_by_reminder` 的批次版；回傳受影響的筆數。

        排程器每一輪都可能需要作廢好幾筆規則底下的紀錄（見
        `MedicationScheduler.process_ticks` 的「無有效藥品」判定），逐筆呼叫
        會讓寫入次數隨規則數線性增加，而這是每 60 秒一次的迴圈。

        `scheduled_from` 是可選的下界（含），用來把作廢範圍限制在當日已展開的
        紀錄。不帶時會作廢該批規則底下所有還沒確認的紀錄——排程器一律會帶，
        理由是「這個時段今天沒有有效藥品」只是今天的判斷，不該回頭動到更早
        的紀錄（那些紀錄當時可能確實有藥）。

        條件同樣限定 `status="pending"`，理由見 `cancel_pending_by_reminder`。
        """
        if not reminder_ids:
            return 0
        if collection is None:
            collection = MongoDBManager.get_medication_logs_collection()
        query: dict = {"reminder_id": {"$in": reminder_ids}, "status": "pending"}
        if scheduled_from is not None:
            query["scheduled_at"] = {"$gte": scheduled_from}
        result = await collection.update_many(query, {"$set": {"status": "cancelled"}})
        return result.modified_count

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
    #
    # 三個 claim 都必須把「清單查詢用過的 status 條件」再斷言一次，不能只看旗標。
    # 清單查出來的那一刻起，結果就已經是過期資料：排程器是「查清單 → 逐筆搶佔 →
    # 推播」，每一筆的搶佔都夾在前面幾筆的 profile 查詢與 LINE 推播之後，使用者
    # 完全有時間在這段空檔按下「我已用藥」。搶佔若不重驗 status，就會發生
    #   1. 已經確認用藥的人收到 T+20「您尚未點擊我已用藥」；
    #   2. 家屬收到 T+30 逾時警報，而且 claim_caregiver_alert 會把 status 從
    #      taken 蓋回 missed，連正確的用藥紀錄一起毀掉。
    # 加上 status 條件之後，兩種先後次序都正確：確認先落地則搶佔失敗、不推播；
    # 搶佔先落地則該筆在當下確實仍未服藥（警報屬實），mark_as_taken 允許 missed
    # 轉 taken，紀錄最終仍收斂成 taken。

    # ── 推播重試上限 ──────────────────────────────────────────────────
    #
    # 推播失敗時把旗標還回去，下一個 tick 就會重新搶佔並重試。這對瞬時故障是對的，
    # 但對不會自行恢復的錯誤（LINE 月額度耗盡的 429、收件人已封鎖官方帳號）則是
    # 每 60 秒一次、直到月底都不會停的無效重試。
    #
    # 所以每個階段各帶一個嘗試次數，由 `_release_push_claim` 在每次還原時累加；
    # 達到 `MAX_PUSH_ATTEMPTS` 就不再還原旗標，該階段就此放棄。放棄之後旗標維持
    # 「已送出」，這在資料上確實不精確（其實沒送成功），但真正的替代方案是無限
    # 重試，那個代價更大；而且嘗試次數本身留在紀錄裡，事後查得出來是放棄還是送達。
    #
    # 三個階段各自獨立計次：T+0 耗盡預算不影響 T+20 與 T+30 各自的重試機會。

    @staticmethod
    async def _release_push_claim(
        log_id: str,
        *,
        stage: str,
        sent_field: str,
        attempts_field: str,
        extra_filter: Optional[dict] = None,
        extra_set: Optional[dict] = None,
    ) -> bool:
        """還原某個階段的推播權，並累加嘗試次數；達到上限時不還原。

        回傳 True 代表旗標已還原、下一個 tick 會重試；False 代表沒有還原
        （已達上限而放棄，或這筆紀錄已不符合還原條件）。

        先 `$inc` 再視結果決定要不要清掉旗標，分兩次寫入而不是一次
        條件式更新：`{"$lt": N}` 對「欄位不存在」的舊紀錄不成立，用單一條件式
        更新會讓所有既有紀錄第一次就被判定為已達上限而直接放棄。`$inc` 沒有
        這個問題（缺欄位視為 0），因此以它的回傳值當判斷依據。這條路徑只在
        推播已經失敗時才走到，多一次往返不影響正常流程。
        """
        col = MongoDBManager.get_medication_logs_collection()
        doc = await col.find_one_and_update(
            {"_id": log_id, sent_field: True, **(extra_filter or {})},
            {"$inc": {attempts_field: 1}},
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            return False

        attempts = doc.get(attempts_field, 0)
        if attempts >= MAX_PUSH_ATTEMPTS:
            logger.error(
                "[MedicationLogRepository] Giving up %s for log %s after %d attempts; "
                "the push flag stays set so it will not be retried",
                stage,
                log_id,
                attempts,
            )
            return False

        result = await col.update_one(
            {"_id": log_id},
            {"$set": {sent_field: False, **(extra_set or {})}},
        )
        return result.modified_count > 0

    @staticmethod
    async def claim_patient_reminder(log_id: str) -> bool:
        """搶下 T+0min 首刷提醒的推播權，回傳 True 代表本實例取得推播權。"""
        col = MongoDBManager.get_medication_logs_collection()
        result = await col.update_one(
            {"_id": log_id, "status": "pending", "patient_reminder_sent": False},
            {"$set": {"patient_reminder_sent": True}},
        )
        return result.modified_count > 0

    @staticmethod
    async def release_patient_reminder(log_id: str) -> bool:
        """推播失敗時還原 T+0min 首刷提醒的旗標，讓下一個 tick 重試。

        重試次數有上限，見上方「推播重試上限」段落。
        """
        return await MedicationLogRepository._release_push_claim(
            log_id,
            stage="T+0min patient reminder",
            sent_field="patient_reminder_sent",
            attempts_field="patient_reminder_attempts",
        )

    @staticmethod
    async def claim_patient_urgent_reminder(log_id: str) -> bool:
        """搶下 T+20min 催促提醒的推播權。

        `status: "pending"` 是必要條件，不是多餘的保險：見上方「推播權搶佔」
        段落——少了它，剛按完「我已用藥」的人會收到「您尚未點擊我已用藥」。
        """
        col = MongoDBManager.get_medication_logs_collection()
        result = await col.update_one(
            {"_id": log_id, "status": "pending", "urgent_reminder_sent": False},
            {"$set": {"urgent_reminder_sent": True}},
        )
        return result.modified_count > 0

    @staticmethod
    async def release_patient_urgent_reminder(log_id: str) -> bool:
        """推播失敗時還原 T+20min 催促提醒的旗標。

        重試次數有上限，見上方「推播重試上限」段落。
        """
        return await MedicationLogRepository._release_push_claim(
            log_id,
            stage="T+20min urgent reminder",
            sent_field="urgent_reminder_sent",
            attempts_field="urgent_reminder_attempts",
        )

    @staticmethod
    async def claim_caregiver_alert(log_id: str) -> bool:
        """搶下 T+30min 家屬警報的推播權，同時把狀態設為 missed。

        `status: "pending"` 是必要條件，不是多餘的保險：見上方「推播權搶佔」
        段落。少了它，使用者在「清單查出來」與「這一筆輪到搶佔」之間按下
        確認時，家屬仍會收到逾時警報，而且 `$set` 的 status 會把 taken 蓋回
        missed——正確的用藥紀錄被推播流程毀掉，比多推一則更嚴重。
        `release_caregiver_alert` 早就有同一個防呆（它的 filter 帶
        `status: "missed"`），搶佔這一端不能是唯一的缺口。
        """
        col = MongoDBManager.get_medication_logs_collection()
        result = await col.update_one(
            {"_id": log_id, "status": "pending", "caregiver_alert_sent": False},
            {"$set": {"caregiver_alert_sent": True, "status": "missed"}},
        )
        return result.modified_count > 0

    @staticmethod
    async def release_caregiver_alert(log_id: str) -> bool:
        """
        推播失敗時還原 T+30min 家屬警報的旗標。

        status 只在仍為 missed 時才回寫 pending：使用者可能在推播失敗的空檔按下
        「已用藥」，那時 status 是 taken，不能被還原動作蓋回去。

        重試次數有上限，見上方「推播重試上限」段落。放棄時 status 維持 missed
        ——使用者確實沒有在時限內確認服藥，這是事實，不因為通知送不出去而改變；
        變的只是家屬沒收到通知，那件事記在 `caregiver_alert_attempts` 裡。
        """
        return await MedicationLogRepository._release_push_claim(
            log_id,
            stage="T+30min caregiver alert",
            sent_field="caregiver_alert_sent",
            attempts_field="caregiver_alert_attempts",
            extra_filter={"status": "missed"},
            extra_set={"status": "pending"},
        )

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
        """列出使用者的用藥歷史，不含已註銷的紀錄。

        `cancelled` 只是為了擋住排程器在同一天的後續 tick 把紀錄重新 upsert 回
        `pending` 而留下的內部記帳，不是使用者做過的事。列出來的話，使用者會在
        歷史裡看到一筆自己從未互動、狀態也無從解讀的紀錄。
        """
        col = MongoDBManager.get_medication_logs_collection()
        cursor = (
            col.find({"user_id": user_id, "status": {"$ne": "cancelled"}})
            .sort("scheduled_at", -1)
            .limit(limit)
        )
        docs = await cursor.to_list(length=None)
        return [MedicationLog(**{**doc, "_id": str(doc["_id"])}) for doc in docs]
