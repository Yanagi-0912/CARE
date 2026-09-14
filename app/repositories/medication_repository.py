import logging
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional
from bson import ObjectId
from pydantic import ValidationError
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db.mongodb import MongoDBManager
from app.models.medication import (
    TAIPEI_TZ,
    Medication,
    MedicationLog,
    MedicationReminder,
)
# 重試上限與還原邏輯與掛號提醒共用，定義在 push_claim；這裡的名稱保留給既有的 import。
from app.repositories.push_claim import MAX_PUSH_ATTEMPTS, release_push_claim

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


def _reminder_from_doc(doc: dict) -> Optional[MedicationReminder]:
    """把一份 Mongo 文件轉成 `MedicationReminder`，解析失敗時只記錄並回傳
    `None`，不往外拋。

    模型現在會驗證 `entries`（`meal_timing` 不能重複、一個條目只能對應一種
    服藥時機，見 `MedicationReminder` 的驗證器），代表「文件存在、但存不進
    模型」不再只是理論上的可能——不論是手動修過的資料、遷移腳本留下的半成
    品，還是未來某個寫入路徑的 bug，都可能讓一份文件變成這種形狀。這幾個
    讀取方法（`list_active_reminders_up_to_time`／`list_reminders_by_user`／
    `list_reminders_by_creator`／`find_by_ids`）在展開前是逐一 `dict → model`
    的迴圈，若不隔離每一份文件各自的例外，一則壞規則就會讓
    `MedicationReminder(**doc)` 拋出，整個列表推導中斷、整批呼叫失敗——對排
    程器來說即是那一個 tick 的所有使用者都收不到推播，代價遠高於「跳過這一
    筆壞規則」。因此壞文件在這裡就地降級成 `None`，呼叫端只需要跳過它。
    """
    try:
        return MedicationReminder(**{**doc, "_id": str(doc["_id"])})
    except ValidationError as exc:
        logger.error(
            "[MedicationReminderRepository] 略過無法解析的規則 %s: %s",
            doc.get("_id"),
            exc,
        )
        return None


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

        新插入的文件同時合成一個單一 `none` 條目（`entries`）與
        `timeout_anchor_time`，值與 `scheduled_time` 相同——這是唯一條目時
        兩個派生欄位理應相等的情況，等同 `derive_entry_fields([單一 none
        條目])` 的結果，只是寫在這裡而不必真的建構 `MedicationReminder`
        再拆解：新建立的規則本來就只有這一種條目，不需要繞一圈。
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
                    "timeout_anchor_time": scheduled_time,
                    "entries": [
                        {
                            "meal_timing": "none",
                            "scheduled_time": scheduled_time,
                            "medication_ids": [],
                        }
                    ],
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
        reminders = [_reminder_from_doc(doc) for doc in docs]
        return [reminder for reminder in reminders if reminder is not None]

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
            reminder = _reminder_from_doc(doc)
            if reminder is not None:
                reminders.append(reminder)
        return reminders

    @staticmethod
    async def list_reminders_by_creator(creator_user_id: str) -> List[MedicationReminder]:
        col = MongoDBManager.get_medication_reminders_collection()
        cursor = col.find({"creator_user_id": creator_user_id})
        docs = await cursor.to_list(length=None)
        reminders = []
        for doc in docs:
            reminder = _reminder_from_doc(doc)
            if reminder is not None:
                reminders.append(reminder)
        return reminders

    @staticmethod
    async def list_active_reminders_up_to_time(
        max_scheduled_time: str, target_date_str: Optional[str] = None
    ) -> List[MedicationReminder]:
        """
        查詢當日已到達排程時間 (scheduled_time <= max_scheduled_time) 且為啟用狀態的提醒規則。

        排程器每個 tick 都呼叫這裡展開全體使用者的規則；單一文件解析失敗
        （見 `_reminder_from_doc`）只跳過那一筆，不能讓一則壞規則拖垮整個
        tick、讓所有使用者那一輪都收不到推播。
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
            reminder = _reminder_from_doc(doc)
            if reminder is not None:
                reminders.append(reminder)
        return reminders

    @staticmethod
    async def update_reminder(
        reminder_id: str,
        update_data: dict,
        collection: Optional[Any] = None,
    ) -> Optional[MedicationReminder]:
        if collection is None:
            collection = MongoDBManager.get_medication_reminders_collection()
        now = datetime.now(tz=timezone.utc)
        # 收到什麼就寫什麼，不再過濾 None。過濾 None 是「清空 end_date」失效的
        # 第二道濾網（第一道在 MedicationService.update_reminder 的 model_dump），
        # 只修其中一層完全沒有效果。哪些欄位允許 null 由服務層界定並在那裡擋成
        # 400，這一層不重複做那個判斷——分散在兩處只會讓兩邊都以為對方有擋。
        # 條目化之後這裡也不重複做的判斷再多一項：`entries` 對應的
        # `scheduled_time`／`timeout_anchor_time`／`medication_ids` 三個派生
        # 欄位由服務層呼叫 `derive_entry_fields` 算好、與 `entries` 一起放進
        # `update_data`，這裡只管照單全收地寫入。
        update_doc = dict(update_data)
        update_doc["updated_at"] = now

        result = await collection.update_one({"_id": reminder_id}, {"$set": update_doc})
        if result.matched_count == 0:
            return None
        doc = await collection.find_one({"_id": reminder_id})
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return MedicationReminder(**doc)

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
        """把藥品掛到既有時段規則的 `none` 條目上（藥袋提交路徑；見 design
        決策 3——藥袋辨識不分飯前飯後，一律掛進 `none`）。

        改成三個各自原子、冪等的更新，而不是像過去那樣直接寫頂層
        `medication_ids`（那個寫法在條目化之後不成立：`medication_ids`
        是由 `derive_entry_fields` 算出來的聯集，SHALL NOT 被直接寫入）：

        1. 舊文件相容：只有「這份文件根本沒有 entries 欄位」（本變更前寫入
           的規則）才會命中，把它補上與現有 `scheduled_time`／
           `medication_ids` 對齊的單一 `none` 條目——之後這份文件就跟新
           規則走同一套條目模型。
        2. `none` 條目補位：文件已有 entries（不論是剛被步驟 1 補上的，
           還是新規則本來就有的），但還沒有 `meal_timing == "none"` 的
           條目（例如使用者已經設定了飯前／飯後、但還沒有任何「無關聯」
           的藥）時，push 一個空的 `none` 條目，時刻取現有規則的
           `scheduled_time`——見下方說明，為什麼這個時刻不需要與最早的
           條目時刻重算比較。
        3. 掛藥：把 `medication_ids` 用 `$addToSet` 併入 `none` 條目
           （`array_filters` 鎖定 `meal_timing == "none"` 的那個元素，
           避免陣列位置法在條目順序不固定時對錯位置），同時比照舊行為
           把同一批 id 併入頂層 `medication_ids`（走到這裡都已經是幾乎
           必然在陣列裡才對，`$addToSet` 只是保險）。

        三步都各自是單一 document 的原子更新，且用 `$addToSet`／只在條件
        不成立時才生效的 filter，重複呼叫（例如同一份藥袋重試送出）結果
        不變，冪等。

        `none` 條目的時刻恆等於規則現有的 `scheduled_time`（規則新建時
        `find_or_create_reminder` 就是這樣合成的；使用者若之後另外用
        detailed 檢視新增了更早的飯前條目，那筆更新會經過
        `derive_entry_fields` 整份重算，`none` 條目的時刻也會一起被那次
        更新改掉，不會停留在這裡寫入的舊值）——因此掛藥本身不需要、也
        不應該去重算 `scheduled_time`／`timeout_anchor_time`：min/max
        的候選集合沒有變，只是其中一個候選（`none` 條目）的
        `medication_ids` 多了幾個 id，時刻不變則兩個派生時刻必然不變。
        """
        if not medication_ids:
            return False
        if collection is None:
            collection = MongoDBManager.get_medication_reminders_collection()

        reminder_doc = await collection.find_one({"_id": reminder_id})
        if not reminder_doc:
            return False
        reminder_scheduled_time = reminder_doc.get("scheduled_time", "08:00")

        # 步驟 1：舊文件補上 entries（只有真的缺這個欄位的文件才會命中）。
        await collection.update_one(
            {"_id": reminder_id, "entries": {"$exists": False}},
            {
                "$set": {
                    "entries": [
                        {
                            "meal_timing": "none",
                            "scheduled_time": reminder_scheduled_time,
                            "medication_ids": reminder_doc.get("medication_ids", []),
                        }
                    ]
                }
            },
        )

        # 步驟 2：還沒有 none 條目時補一個空的（步驟 1 剛補上的文件已經有了，
        # 這裡不會再命中；原本就有飯前/飯後但沒有 none 的文件才會命中）。
        # 單一 document 的 update_one 對同一份文件而言，filter 的判定與寫入
        # 是同一個原子操作的一部分，兩個並行的掛藥請求不可能同時判定「這份
        # 文件還沒有 none 條目」為真而各自 push 一次，不會重複 push。
        await collection.update_one(
            {"_id": reminder_id, "entries.meal_timing": {"$ne": "none"}},
            {
                "$push": {
                    "entries": {
                        "meal_timing": "none",
                        "scheduled_time": reminder_scheduled_time,
                        "medication_ids": [],
                    }
                }
            },
        )

        # 步驟 3：把藥掛進 none 條目與頂層聯集欄位。
        result = await collection.update_one(
            {"_id": reminder_id},
            {
                "$addToSet": {
                    "entries.$[none].medication_ids": {"$each": medication_ids},
                    "medication_ids": {"$each": medication_ids},
                },
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
            array_filters=[{"none.meal_timing": "none"}],
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
    async def create_one(
        medication: Medication, collection: Optional[Any] = None
    ) -> Medication:
        """手動新增單一藥品（`POST /medications`）。包 `create_many`而不是
        另外寫一份 insert_one 邏輯——id 指派、`model_copy` 回填都只有一份
        實作，`create_many` 已經處理好空清單與批次兩種情況。"""
        created = await MedicationRepository.create_many(
            [medication], collection=collection
        )
        return created[0]

    @staticmethod
    async def list_by_user(
        user_id: str, collection: Optional[Any] = None
    ) -> List[Medication]:
        """列出某位使用者的全部藥品，不篩 `enabled` 與日期區間（`GET
        /medications?user_id=`，見 design 決策 9）。

        與 `list_active_by_user` 刻意不同：那支給推播與消息索引用，只要
        「當下有效」的藥；這支給 LIFF 詳細設定頁的「新增藥品」清單挑選，
        使用者需要看到全部藥品（含已停用者，前端自行以 `enabled` 標示），
        否則會誤以為某顆藥從未建立過而重複新增。

        依 `created_at` 升冪：新增藥品時使用者多半是照著藥袋或處方順序
        一顆一顆加，清單維持建立順序比較符合預期，也讓同一批藥袋辨識
        新增的藥品在清單裡保持原本的相對順序。
        """
        if collection is None:
            collection = MongoDBManager.get_medications_collection()
        cursor = collection.find({"user_id": user_id}).sort("created_at", 1)
        docs = await cursor.to_list(length=None)
        return [Medication(**{**doc, "_id": str(doc["_id"])}) for doc in docs]

    @staticmethod
    async def list_active_drug_keys(
        date_str: str, collection: Optional[Any] = None
    ) -> List[str]:
        """當日仍有效的所有藥品，其藥名與學名的不重複聯集。

        **這支查詢刻意不帶 `user_id`。** 每日消息索引要的是「全體使用者總共在吃
        哪些藥」，與是誰在吃無關——同一個藥名搜出來的官方消息對所有服用者都一樣，
        因此快取的鍵是藥名而非使用者，成本才會是 O(不重複藥數) 而不是 O(使用者數)
        （見 openspec/changes/medical-news-push/design.md 決策 2）。加上 user_id
        會讓這個前提消失。

        `name` 與 `generic_name` 取聯集而非二選一：藥袋上印的常是品牌短名，而官方
        公告常以成分名發布（「含 ACETAMINOPHEN 之藥品」），兩邊都要能比對得到。
        """
        if collection is None:
            collection = MongoDBManager.get_medications_collection()

        query = {"enabled": True, "$and": _active_date_window(date_str)}
        names = await collection.distinct("name", query)
        generics = await collection.distinct("generic_name", query)

        seen: List[str] = []
        for value in list(names) + list(generics):
            if not value or not str(value).strip():
                continue
            cleaned = str(value).strip()
            if cleaned not in seen:
                seen.append(cleaned)
        return seen

    @staticmethod
    async def list_active_by_user(
        user_id: str, date_str: str, collection: Optional[Any] = None
    ) -> List[Medication]:
        """某位使用者當日仍有效的藥品。

        日期濾網沿用 `_active_date_window`，與 `find_active_by_ids` 是同一套判定——
        療程已結束的藥不該讓使用者收到「與您正在服用的藥有關」的消息卡。
        """
        if collection is None:
            collection = MongoDBManager.get_medications_collection()

        query = {
            "user_id": user_id,
            "enabled": True,
            "$and": _active_date_window(date_str),
        }
        cursor = collection.find(query)
        docs = await cursor.to_list(length=None)
        return [Medication(**{**doc, "_id": str(doc["_id"])}) for doc in docs]

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

    @staticmethod
    async def list_visits(user_id: str, collection=None) -> List[dict]:
        """把使用者的用藥紀錄彙整成「看診紀錄」。

        **以 `(institution, dispensed_date)` 分組，不是以掃描分組。** 實測同一
        個藥袋在 42 分鐘內被掃了三次，產生三筆 `draft_id` 但只有一次看診——
        按掃描列會讓畫面上同一家醫院出現三次，而長輩只去了一次。

        日期取藥袋上印的 `dispensed_date` 而非 `created_at`：長輩可能拖三天
        才掃，掃描時間不是看診日。

        **兩者皆缺的藥不成為一次看診**，而是歸入 `institution=None` 的那一組：
        手動新增的藥、以及本欄位落地之前的舊紀錄都沒有這兩個值，把它們各自
        當成獨立看診會產生一堆空白列。呈現面據此顯示「未記錄來源」。

        回傳依日期新到舊排序；同一天有多家機構時，機構名字典序固定，讓同一
        份資料永遠產生同一個畫面順序。
        """
        if collection is None:
            collection = MongoDBManager.get_medications_collection()

        pipeline = [
            {"$match": {"user_id": user_id}},
            {
                "$group": {
                    "_id": {
                        "institution": "$institution",
                        "dispensed_date": "$dispensed_date",
                    },
                    "medication_ids": {"$push": "$_id"},
                    "medication_names": {"$push": "$name"},
                    "draft_ids": {"$addToSet": "$draft_id"},
                    "first_created_at": {"$min": "$created_at"},
                }
            },
            # dispensed_date 是 YYYY-MM-DD 字串，字典序即時序。null 排在最後
            # （MongoDB 的 null 小於任何字串，降冪排序時自然沉底）——沒有日期
            # 的那組是「未記錄來源」，不該擠在最上面。
            {"$sort": {"_id.dispensed_date": -1, "_id.institution": 1}},
        ]
        rows = await collection.aggregate(pipeline).to_list(length=None)
        return [
            {
                "institution": row["_id"].get("institution"),
                "dispensed_date": row["_id"].get("dispensed_date"),
                "medication_ids": row.get("medication_ids") or [],
                "medication_names": [n for n in (row.get("medication_names") or []) if n],
                "scan_count": len([d for d in (row.get("draft_ids") or []) if d]),
                "first_created_at": row.get("first_created_at"),
            }
            for row in rows
        ]


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

        # 依人、依時間查用藥歷史（查服藥狀況，見 list_logs_by_user_between）。沒有這個
        # 索引，依人查詢會掃過整張表。與上面分開處理：這個建不起來只會變慢，不該跟
        # 唯一索引的錯誤訊息混在一起。
        try:
            await col.create_index(
                [("user_id", 1), ("scheduled_at", 1)],
                name="user_scheduled",
            )
        except Exception:
            logger.exception(
                "[MedicationLogRepository] 無法建立 medication_logs 的 (user_id, scheduled_at) 索引"
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
    async def mark_as_taken(
        log_id: str,
        taken_at: Optional[datetime] = None,
        taken_medication_ids: Optional[List[str]] = None,
        collection: Optional[Any] = None,
    ) -> Optional[MedicationLog]:
        """更新單一 Document 狀態為已服藥（「全部已服用」整批確認路徑，見
        design 決策 4）。

        `pending`、`missed`、`cancelled` 三種狀態都允許轉成 `taken`：使用者按下的
        確認一律優先於系統推得的狀態。`missed` 是排程器判定的逾時，`cancelled` 是
        規則被關閉時的註銷——兩者都可能發生在使用者其實已經服藥之後（先吃了藥，
        才進 LIFF 關掉這個時段，最後才想起來按推播訊息上還留著的【我已用藥】）。
        真的吃過藥是事實，紀錄應該收斂成 `taken`。

        放寬狀態條件不會讓已經停下的推播復活：三階推播的待推播查詢限定
        `status="pending"`，`taken` 同樣挑不到。

        `taken_medication_ids` 有給時併入同一個 `$set` 更新（用
        `$addToSet`／`$each`）：整批確認要把「當時有效的藥品」全部寫進
        `taken_medication_ids`，讓用藥歷史能一致地回答「那次吃了什麼」
        （design 決策 4）；呼叫端（service 層）負責決定要傳哪些 id，這裡
        只管寫入。
        """
        if collection is None:
            collection = MongoDBManager.get_medication_logs_collection()
        now = taken_at or datetime.now(tz=timezone.utc)
        update: dict = {"$set": {"status": "taken", "taken_at": now}}
        if taken_medication_ids:
            update["$addToSet"] = {
                "taken_medication_ids": {"$each": taken_medication_ids}
            }
        result = await collection.update_one(
            {"_id": log_id, "status": {"$in": ["pending", "missed", "cancelled"]}},
            update,
        )
        if result.matched_count == 0:
            doc = await collection.find_one({"_id": log_id})
            if doc:
                doc["_id"] = str(doc["_id"])
                log = MedicationLog(**doc)
                if log.status == "taken":
                    return log
            return None
        doc = await collection.find_one({"_id": log_id})
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return MedicationLog(**doc)

    @staticmethod
    async def add_taken_medication(
        log_id: str,
        medication_id: str,
        collection: Optional[Any] = None,
    ) -> Optional[MedicationLog]:
        """逐藥確認累積單一藥品 id（見 design 決策 4「逐藥確認」）。

        用 `$addToSet` 而非 `$push`：重複按同一顆藥的【已吃】必須是冪等的
        （design 決策 6「重複按同一顆是冪等的，回覆相同」），`$push` 會讓
        同一個 id 在陣列裡出現多次，「全部到齊」的判定（集合包含關係）雖然
        不受影響，但會讓紀錄裡的陣列無意義地增長。

        不像 `mark_as_taken` 限定 `status in (pending, missed, cancelled)`：
        逐藥確認允許在任何狀態下寫入——包含已經 `taken` 之後又被按到（例如
        使用者對著同一則訊息重複點擊），因為「全部到齊」判定在服務層重算，
        這裡只負責忠實記錄使用者按過哪些藥，不做狀態機的把關。
        """
        if collection is None:
            collection = MongoDBManager.get_medication_logs_collection()
        await collection.update_one(
            {"_id": log_id},
            {"$addToSet": {"taken_medication_ids": medication_id}},
        )
        doc = await collection.find_one({"_id": log_id})
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return MedicationLog(**doc)

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
    async def resync_pending_by_reminder(
        reminder_id: str,
        scheduled_at: datetime,
        slot_type: str,
        urgent_at: Optional[datetime] = None,
        timeout_at: Optional[datetime] = None,
        collection: Optional[Any] = None,
    ) -> tuple[int, int]:
        """讓某筆規則當日還沒確認的紀錄對齊改過的排程，回傳 `(註銷數, 改標數)`。

        `urgent_at`／`timeout_at` 是條目化之後新增的對齊維度（design 決策 5
        第二種情形）：最早時刻（`scheduled_time`）不變、只有最晚時刻
        （`timeout_anchor_time`）變了時，紀錄的 `scheduled_at` 不用動
        （排程展開的 T+0 時刻沒變），但 T+20／T+30 的推播基準要跟著改，
        否則會用舊的最晚時刻催促或通知家屬逾時。這種情形走的正是「時刻
        相同、只有標籤變了」的改標路徑，只是這次要改的標籤除了
        `slot_type`（時段名稱互換）還多了這兩個時間欄位。

        呼叫端未帶 `urgent_at`／`timeout_at`（既有呼叫，只改時段名稱）時，
        改標查詢與 `$set` 維持本變更前的形狀（只看 `slot_type` 是否不同、
        只改 `slot_type`），行為不變。帶了其中之一時，改標查詢改成
        `$or`——只要 `slot_type`／`urgent_at`／`timeout_at` 任一項不同就
        命中，`$set` 一併把有帶的欄位寫入，讓「只改了最晚時刻、時段名稱
        沒變」與「只改了時段名稱、最晚時刻沒變」都能各自被對到的那一項
        `$ne` 命中。

        使用者改時段或改提醒時間時呼叫。展開出來的紀錄是規則在**展開當下**的
        快照（`slot_type`、`scheduled_at` 都是複製過去的值），三階推播查詢只讀
        紀錄、不回頭 join 規則（見 `cancel_pending_by_reminder`），所以規則改了
        之後那筆紀錄仍會依舊排程走完 T+20 催促與 T+30 家屬逾時警報。

        兩種情形要分開處理，因為後果不同：

        - **時刻已經不同**（改了提醒時間，或改時段時時間跟著改）：舊時刻不再
          是這筆規則的排程，紀錄註銷。新時刻由排程器照常展開成另一筆紀錄
          ——`upsert_log` 以 `(reminder_id, scheduled_at)` 為唯一識別，時刻不同
          就是不同的紀錄，不會被這裡的註銷波及。
        - **時刻相同、只有時段名稱變了**（例如自訂 07:15 的「早」改成「中」）：
          該吃藥的那一刻沒有變，註銷等於平白吃掉使用者今天的提醒。改標即可
          ——推播文案的時段字樣取自紀錄的 `slot_type`，不改的話今天仍會推成
          「早 服藥時間到了」。

        兩個查詢的條件互斥（`$ne` 與相等），不會重複打到同一筆紀錄。狀態一律
        限定 `pending`：已 `taken` 是使用者真的吃過藥的事實，已 `missed` 的家屬
        警報早已送出，兩者都不能因為改了規則而被改寫（同
        `cancel_pending_by_reminder`）。

        註銷側同樣不帶日期條件，理由亦同：紀錄是惰性展開的，`pending` 只會是
        還在三階推播時間窗內的那一筆。
        """
        if collection is None:
            collection = MongoDBManager.get_medication_logs_collection()

        cancelled = await collection.update_many(
            {
                "reminder_id": reminder_id,
                "status": "pending",
                "scheduled_at": {"$ne": scheduled_at},
            },
            {"$set": {"status": "cancelled"}},
        )

        retag_set: dict = {"slot_type": slot_type}
        if urgent_at is None and timeout_at is None:
            retag_query: dict = {
                "reminder_id": reminder_id,
                "status": "pending",
                "scheduled_at": scheduled_at,
                "slot_type": {"$ne": slot_type},
            }
        else:
            or_clauses: list = [{"slot_type": {"$ne": slot_type}}]
            if urgent_at is not None:
                or_clauses.append({"urgent_at": {"$ne": urgent_at}})
                retag_set["urgent_at"] = urgent_at
            if timeout_at is not None:
                or_clauses.append({"timeout_at": {"$ne": timeout_at}})
                retag_set["timeout_at"] = timeout_at
            retag_query = {
                "reminder_id": reminder_id,
                "status": "pending",
                "scheduled_at": scheduled_at,
                "$or": or_clauses,
            }

        retagged = await collection.update_many(
            retag_query,
            {"$set": retag_set},
        )
        return cancelled.modified_count, retagged.modified_count

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

        實作與掛號提醒共用，見 `app.repositories.push_claim.release_push_claim`。
        """
        return await release_push_claim(
            MongoDBManager.get_medication_logs_collection(),
            log_id,
            stage=stage,
            sent_field=sent_field,
            attempts_field=attempts_field,
            extra_filter=extra_filter,
            extra_set=extra_set,
            log_prefix="[MedicationLogRepository]",
        )

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
    async def list_pending_urgent_reminders(
        threshold_time: datetime, collection: Optional[Any] = None
    ) -> List[MedicationLog]:
        """
        查詢已過 T+20min 且狀態仍為 pending 尚未發送催促提醒的日誌。

        T+20 的基準是 `urgent_at`（= 規則 `timeout_anchor_time` + 20 分鐘，
        見 design 決策 4），不再是 `scheduled_at` + 20 分鐘——飯前飯後拆成
        兩批藥時，最晚那批的服藥時間才是「該催促的時刻」，用最早那批算會
        催得太早。

        但既有紀錄（本變更前展開）沒有 `urgent_at` 這個欄位，所以查詢用
        `$or` 保留舊的計算方式當退回分支：有 `urgent_at` 的走
        `urgent_at <= threshold_time`；沒有的走
        `scheduled_at <= threshold_time - 20min`，等同舊版行為。呼叫端
        （scheduler）改傳現在時刻 `now`，不再自己先減 20 分鐘——20 分鐘的
        位移現在下放到這裡的退回分支，`urgent_at` 分支不需要它（`urgent_at`
        寫入時已經加過 20 分鐘）。

        「沒有這個欄位」在查詢上刻意拆成兩種寫法各配一個分支：`$exists:
        False`（欄位真的不存在，本變更前的紀錄）與 `urgent_at: None`
        （欄位存在但值是 null）。兩者在 MongoDB 裡是不同的文件形狀，
        `$exists: False` 不會命中值為 null 的欄位。這裡的正確性因此不再
        依賴 `upsert_log` 寫入時是否用 `exclude_none=True` 把 None 值濾掉
        不寫入——不論插入路徑將來要不要保留那個過濾，這條查詢兩種形狀都
        接得住，不會因為欄位「存在但是 null」而漏掉一筆該催促的紀錄。

        兩個退回分支都用 $lte：防範排程檢查秒數偏差或伺服器重啟造成的延遲
        漏發。這是過渡期的分支（design Risks）：部署後隔天舊紀錄就會走完
        T+20／T+30 全部收斂為 taken/missed，屆時可以拿掉退回分支，只是
        本次變更範圍不含這個收尾。
        """
        if collection is None:
            collection = MongoDBManager.get_medication_logs_collection()
        legacy_condition = {"scheduled_at": {"$lte": threshold_time - timedelta(minutes=20)}}
        query = {
            "status": "pending",
            "patient_reminder_sent": True,
            "urgent_reminder_sent": False,
            "$or": [
                {"urgent_at": {"$lte": threshold_time}},
                {"urgent_at": {"$exists": False}, **legacy_condition},
                {"urgent_at": None, **legacy_condition},
            ],
        }
        cursor = collection.find(query)
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

    @staticmethod
    async def list_logs_by_user_between(
        user_id: str, start: datetime, end: datetime
    ) -> List[MedicationLog]:
        """列出 [start, end) 之間的用藥歷史，由早到晚，不含已註銷的紀錄（理由同上）。

        查服藥狀況用（見 MedicationStatusService）：一次取回要看的那幾天，再依台北
        日期分組。
        """
        col = MongoDBManager.get_medication_logs_collection()
        cursor = col.find(
            {
                "user_id": user_id,
                "status": {"$ne": "cancelled"},
                "scheduled_at": {"$gte": start, "$lt": end},
            }
        ).sort("scheduled_at", 1)
        docs = await cursor.to_list(length=None)
        return [MedicationLog(**{**doc, "_id": str(doc["_id"])}) for doc in docs]
