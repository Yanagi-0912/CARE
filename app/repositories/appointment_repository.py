"""掛號提醒（appointment_reminders）資料庫操作。

推播權搶佔的設計與 `MedicationLogRepository` 相同，理由也相同（見該檔的「推播權
搶佔」段落）：查清單 → 逐筆搶佔 → 推播之間沒有原子性，多實例並存時會重複推播；
而清單查出來的那一刻起就是過期資料，使用者隨時可能在空檔按下「我已到診」。
所以**每一支 claim 都把清單查詢用過的條件——狀態與時間窗——再斷言一次**，不能只看
旗標：清單與搶佔之間門診可能被改時間，只驗狀態的話，新時間的推播會被提早送出、
旗標也被吃掉。

與用藥不同的一點：這裡是 instance 而非一組 staticmethod，collection 由建構子注入。
測試因此能直接餵假的 collection，不必 monkeypatch 掉 `MongoDBManager`。
"""

import logging
from datetime import datetime
from typing import Any, Callable, List, Optional, Sequence, Tuple, get_args

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db.mongodb import MongoDBManager
from app.models.appointment import (
    CAREGIVER_ALERT_DELAY,
    OPEN_STATUSES,
    PRE_REMINDER_LEAD,
    AppointmentListScope,
    AppointmentReminder,
    AppointmentStatus,
)
from app.repositories.push_claim import release_push_claim

logger = logging.getLogger(__name__)

LOG_PREFIX = "[AppointmentReminderRepository]"

# 佔用門診時段的狀態：取消以外全部。取消了 9/15 09:30 那一筆，要能再建一筆
# 9/15 09:30（與 AppointmentService._ensure_not_duplicate 同一條規則）。
SLOT_HOLDING_STATUSES: tuple[str, ...] = tuple(
    status for status in get_args(AppointmentStatus) if status != "cancelled"
)


class DuplicateAppointmentError(Exception):
    """同一位就診者、同一個門診瞬間已有一筆未取消的提醒，被唯一索引擋下。"""


def _from_doc(doc: dict) -> AppointmentReminder:
    return AppointmentReminder(**{**doc, "_id": str(doc["_id"])})


def past_filter(now: datetime) -> dict:
    """「過去」的唯一定義：當日已結束，或已經取消。

    列表的兩個 scope 與「刪除全部歷史紀錄」都從這裡取，不能各寫一份——否則畫面上
    「過去的門診」顯示 23 筆，按下刪除全部卻刪了 25 筆。「當日結束」與排程器標記
    missed 是同一條界線（`day_end_at`）。一筆下個月的門診取消了，也歸在過去。
    """
    return {"$or": [{"day_end_at": {"$lte": now}}, {"status": "cancelled"}]}


def scope_filter(scope: AppointmentListScope, now: datetime) -> dict:
    """「即將到來」以 `$nor` 取「過去」的補集，而不是另寫一組條件：兩者因此在結構上
    就互斥、合起來涵蓋全部，日後改了「過去」的定義也不會漏掉或重複。"""
    past = past_filter(now)
    return past if scope == "past" else {"$nor": [past]}


# ── 推播階段的挑選條件 ──────────────────────────────────────────────
#
# 清單查詢與搶佔共用同一份，見模組註解。
#
#   T-1h   [T-1h, T+0)        status = scheduled
#   T+0    [T+0,  T+30)       status ∈ {scheduled, departed}
#   T+30   [T+30, 當日結束)   status ∈ {scheduled, departed}


def _pre_window(now: datetime) -> dict:
    return {
        "enabled": True,
        "status": "scheduled",
        "appointment_at": {"$lte": now + PRE_REMINDER_LEAD, "$gt": now},
    }


def _start_window(now: datetime) -> dict:
    return {
        "enabled": True,
        "status": {"$in": list(OPEN_STATUSES)},
        "appointment_at": {"$lte": now, "$gt": now - CAREGIVER_ALERT_DELAY},
        "day_end_at": {"$gt": now},
    }


def _caregiver_window(now: datetime) -> dict:
    return {
        "enabled": True,
        "status": {"$in": list(OPEN_STATUSES)},
        "appointment_at": {"$lte": now - CAREGIVER_ALERT_DELAY},
        "day_end_at": {"$gt": now},
    }


class AppointmentReminderRepository:
    def __init__(
        self,
        collection_provider: Callable[[], Any] = (
            MongoDBManager.get_appointment_reminders_collection
        ),
    ) -> None:
        self._collection_provider = collection_provider

    @property
    def _col(self) -> Any:
        return self._collection_provider()

    async def ensure_indexes(self) -> None:
        """三組查詢各一個索引：列表（user_id）、三個推播階段（status +
        appointment_at）、當日結束的掃描（status + day_end_at）；外加一個部分唯一
        索引，擋同一位就診者同一瞬間的第二筆未取消提醒。

        唯一索引是 `AppointmentService._ensure_not_duplicate` 的原子版本：應用層的
        「先查再寫」擋不住兩個同時送出的請求（連點兩下送出、本人與家人同時建立），
        而重複的那一筆會各自推三個階段。部分索引只涵蓋 `SLOT_HOLDING_STATUSES`，
        取消的那筆不佔時段。以下行為已在 MongoDB 8.0（與 Atlas 同版）實測：
        `$in` 可用於 partialFilterExpression、可與同鍵的一般索引並存、取消後可再建
        同一時段、改時間撞到已佔用的時段會被擋。
        """
        try:
            await self._col.create_index(
                [("user_id", 1), ("appointment_at", 1)], name="user_appointment_at"
            )
            await self._col.create_index(
                [("status", 1), ("appointment_at", 1)], name="status_appointment_at"
            )
            await self._col.create_index(
                [("status", 1), ("day_end_at", 1)], name="status_day_end_at"
            )
        except Exception:
            # 索引只影響查詢效率，不影響正確性；建不起來不該讓 app 起不來。
            logger.exception("%s 無法建立 appointment_reminders 索引", LOG_PREFIX)
        try:
            await self._col.create_index(
                [("user_id", 1), ("appointment_at", 1)],
                name="user_appointment_at_unique_slot",
                unique=True,
                partialFilterExpression={"status": {"$in": list(SLOT_HOLDING_STATUSES)}},
            )
        except Exception:
            # 既有資料裡已有同一瞬間的兩筆（舊規則只擋同醫院、同科別）時建不起來。
            # app 照常啟動，重複檢查退回只有應用層；清掉重複資料後重啟即會建立。
            logger.exception(
                "%s 無法建立掛號時段唯一索引，同時送出的重複建立將擋不住", LOG_PREFIX
            )

    # ── CRUD ──────────────────────────────────────────────────────────

    async def create(self, reminder: AppointmentReminder) -> AppointmentReminder:
        # 不用 exclude_none：nullable 欄位以 null 明確寫入，文件形狀固定。
        doc = reminder.model_dump(by_alias=True)
        if not doc.get("_id"):
            doc["_id"] = str(ObjectId())
        try:
            await self._col.insert_one(doc)
        except DuplicateKeyError as exc:
            raise DuplicateAppointmentError() from exc
        return _from_doc(doc)

    async def get_by_id(self, reminder_id: str) -> Optional[AppointmentReminder]:
        doc = await self._col.find_one({"_id": reminder_id})
        return _from_doc(doc) if doc else None

    async def list_by_user(
        self, user_id: str, *, day_end_after: Optional[datetime] = None
    ) -> List[AppointmentReminder]:
        """某位就診者的提醒，依門診時間由早到晚。

        `day_end_after` 帶值時只回傳「當日結束」還沒到的——也就是今天與未來的門診。
        「過去」與排程器標記 missed 用的是同一條界線，兩處不會各說各話。
        """
        query: dict = {"user_id": user_id}
        if day_end_after is not None:
            query["day_end_at"] = {"$gt": day_end_after}
        cursor = self._col.find(query).sort("appointment_at", 1)
        docs = await cursor.to_list(length=None)
        return [_from_doc(doc) for doc in docs]

    async def list_by_user_at(
        self, user_id: str, appointment_at: datetime
    ) -> List[AppointmentReminder]:
        """同一位就診者、同一個門診瞬間的提醒，給重複檢查用。

        門診時間寫入前一律截到分鐘，所以直接比相等即可。
        """
        cursor = self._col.find({"user_id": user_id, "appointment_at": appointment_at})
        docs = await cursor.to_list(length=None)
        return [_from_doc(doc) for doc in docs]

    async def list_scope(
        self,
        user_id: str,
        scope: AppointmentListScope,
        now: datetime,
        *,
        limit: Optional[int] = None,
        older_than: Optional[Tuple[datetime, str]] = None,
    ) -> List[AppointmentReminder]:
        """某位就診者「即將到來」或「過去」的提醒（定義見 `past_filter`）。

        upcoming 由早到晚，past 由新到舊；同一時間多筆時以 id 決定先後，分頁位置才
        唯一。`older_than` 是上一頁最後一筆的（門診時間, id），只用於 past：回傳排在
        它之後、也就是更舊的那些。
        """
        direction = -1 if scope == "past" else 1
        clauses = [scope_filter(scope, now)]
        if older_than is not None:
            at, last_id = older_than
            clauses.append(
                {
                    "$or": [
                        {"appointment_at": {"$lt": at}},
                        {"appointment_at": at, "_id": {"$lt": last_id}},
                    ]
                }
            )
        cursor = self._col.find({"user_id": user_id, "$and": clauses}).sort(
            [("appointment_at", direction), ("_id", direction)]
        )
        if limit is not None:
            cursor = cursor.limit(limit)
        docs = await cursor.to_list(length=None)
        return [_from_doc(doc) for doc in docs]

    async def count_scope(
        self, user_id: str, scope: AppointmentListScope, now: datetime
    ) -> int:
        return await self._col.count_documents(
            {"user_id": user_id, **scope_filter(scope, now)}
        )

    async def update_fields(
        self,
        reminder_id: str,
        set_doc: dict,
        *,
        only_if_status_in: Optional[Sequence[str]] = None,
    ) -> Optional[AppointmentReminder]:
        """收到什麼就 `$set` 什麼，包含 None。哪些欄位允許 null 由服務層界定。

        `only_if_status_in` 帶值時是條件式寫入：狀態不在其中就不寫、回 None。改門診
        時間靠它封住「讀到現況」與「寫入」之間的空檔——那段時間裡有人剛回報到診或
        取消，無條件的 `$set` 會把狀態改回 scheduled，洗掉到診或取消的紀錄。
        """
        query: dict = {"_id": reminder_id}
        if only_if_status_in is not None:
            query["status"] = {"$in": list(only_if_status_in)}
        try:
            doc = await self._col.find_one_and_update(
                query, {"$set": set_doc}, return_document=ReturnDocument.AFTER
            )
        except DuplicateKeyError as exc:
            # 改到的時間已有另一筆未取消的提醒，而且是在服務層檢查之後才出現的。
            raise DuplicateAppointmentError() from exc
        return _from_doc(doc) if doc else None

    async def delete(self, reminder_id: str) -> bool:
        result = await self._col.delete_one({"_id": reminder_id})
        return result.deleted_count > 0

    async def delete_past(self, user_id: str, now: datetime) -> int:
        """刪除某位就診者「過去」的全部提醒（定義見 `past_filter`），回傳實際筆數。

        單一 `delete_many`，不是呼叫端逐筆刪。它不是跨文件的交易：命令在伺服器端
        中途失敗時，已刪的不會還原。但條件只挑得到過去的紀錄，重送同一個請求只會
        把剩下的刪掉，不會多刪任何一筆即將到來的門診。
        """
        result = await self._col.delete_many({"user_id": user_id, **past_filter(now)})
        return result.deleted_count

    # ── 狀態轉移 ──────────────────────────────────────────────────────
    #
    # 三支都是「以來源狀態為條件的單一文件原子更新」：本人與家屬同時按下同一顆
    # 按鈕（或一人按到診、一人按取消）時，只有一邊會寫入，另一邊拿到 None，由服務
    # 層重讀後判斷是冪等（已經是目標狀態）還是衝突。
    #
    # 條件都含 `day_end_at > at`，不能只看狀態：當日結束之後、排程器下一次標記
    # missed 之前，狀態仍是 scheduled／departed。只看狀態的話，隔天凌晨按一下
    # 「我已到診」就能把沒去的門診補登成到診（已拍板不做補登），也能取消一個已經
    # 結束的門診。

    async def mark_departed(
        self, reminder_id: str, *, by_user_id: str, at: datetime
    ) -> Optional[AppointmentReminder]:
        doc = await self._col.find_one_and_update(
            {"_id": reminder_id, "status": "scheduled", "day_end_at": {"$gt": at}},
            {
                "$set": {
                    "status": "departed",
                    "departed_at": at,
                    "departed_by_user_id": by_user_id,
                    "updated_at": at,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return _from_doc(doc) if doc else None

    async def mark_attended(
        self, reminder_id: str, *, by_user_id: str, at: datetime
    ) -> Optional[AppointmentReminder]:
        doc = await self._col.find_one_and_update(
            {
                "_id": reminder_id,
                "status": {"$in": list(OPEN_STATUSES)},
                "day_end_at": {"$gt": at},
            },
            {
                "$set": {
                    "status": "attended",
                    "attended_at": at,
                    "attended_by_user_id": by_user_id,
                    "updated_at": at,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return _from_doc(doc) if doc else None

    async def mark_cancelled(
        self, reminder_id: str, *, by_user_id: str, at: datetime
    ) -> Optional[AppointmentReminder]:
        """只寫狀態與取消者。三個推播階段的查詢與搶佔都只挑 scheduled／departed，
        寫入 cancelled 之後尚未發出的推播自然全部停下，不需要另外清旗標。"""
        doc = await self._col.find_one_and_update(
            {
                "_id": reminder_id,
                "status": {"$in": list(OPEN_STATUSES)},
                "day_end_at": {"$gt": at},
            },
            {
                "$set": {
                    "status": "cancelled",
                    "cancelled_at": at,
                    "cancelled_by_user_id": by_user_id,
                    "updated_at": at,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return _from_doc(doc) if doc else None

    # ── 排程器：三個推播階段 ──────────────────────────────────────────
    #
    # 每個階段都有一個時間窗，窗口之外的就不再推播（等同用藥的 misfire grace）：
    #
    #   T-1h   [T-1h, T+0)        status = scheduled
    #   T+0    [T+0,  T+30)       status ∈ {scheduled, departed}
    #   T+30   [T+30, 當日結束)   status ∈ {scheduled, departed}
    #
    # 停機跨過某個窗口時，那個階段直接跳過，不會在重啟的第一個 tick 連續補推三則。
    # 提醒建立得晚（例如門診前 20 分鐘才建立）時，T-1h 會在下一個 tick 送出——
    # 窗口還沒過，「我已出發」的按鈕仍有意義。

    async def _list(self, query: dict) -> List[AppointmentReminder]:
        cursor = self._col.find(query)
        docs = await cursor.to_list(length=None)
        return [_from_doc(doc) for doc in docs]

    async def _claim(self, query: dict, sent_field: str) -> bool:
        result = await self._col.update_one(query, {"$set": {sent_field: True}})
        return result.modified_count > 0

    async def list_due_pre_reminders(self, now: datetime) -> List[AppointmentReminder]:
        return await self._list({**_pre_window(now), "pre_reminder_sent": False})

    async def claim_pre_reminder(self, reminder_id: str, now: datetime) -> bool:
        """`status: scheduled` 不是多餘的：已經按了出發的人不該再收到「出發了嗎」。"""
        return await self._claim(
            {"_id": reminder_id, **_pre_window(now), "pre_reminder_sent": False},
            "pre_reminder_sent",
        )

    async def release_pre_reminder(self, reminder_id: str) -> bool:
        return await release_push_claim(
            self._col,
            reminder_id,
            stage="T-1h pre reminder",
            sent_field="pre_reminder_sent",
            attempts_field="pre_reminder_attempts",
            log_prefix=LOG_PREFIX,
        )

    async def list_due_start_reminders(self, now: datetime) -> List[AppointmentReminder]:
        return await self._list({**_start_window(now), "start_reminder_sent": False})

    async def claim_start_reminder(self, reminder_id: str, now: datetime) -> bool:
        """`status` 條件擋的是「剛按完我已到診，又收到門診時間到了」。"""
        return await self._claim(
            {"_id": reminder_id, **_start_window(now), "start_reminder_sent": False},
            "start_reminder_sent",
        )

    async def release_start_reminder(self, reminder_id: str) -> bool:
        return await release_push_claim(
            self._col,
            reminder_id,
            stage="T+0 start reminder",
            sent_field="start_reminder_sent",
            attempts_field="start_reminder_attempts",
            log_prefix=LOG_PREFIX,
        )

    async def list_due_caregiver_alerts(self, now: datetime) -> List[AppointmentReminder]:
        """判定是「不是 attended」，不是「不是 departed」：出發了卻沒到，比從頭
        沒動作更值得家人關心（已拍板）。"""
        return await self._list({**_caregiver_window(now), "caregiver_alert_sent": False})

    async def claim_caregiver_alert(self, reminder_id: str, now: datetime) -> bool:
        """與用藥不同，搶下家屬警報**不改狀態**：錯過（missed）只在當日結束時
        標記，T+30 之後本人或家屬仍然可以回報到診。"""
        return await self._claim(
            {"_id": reminder_id, **_caregiver_window(now), "caregiver_alert_sent": False},
            "caregiver_alert_sent",
        )

    async def release_caregiver_alert(self, reminder_id: str) -> bool:
        return await release_push_claim(
            self._col,
            reminder_id,
            stage="T+30 caregiver alert",
            sent_field="caregiver_alert_sent",
            attempts_field="caregiver_alert_attempts",
            log_prefix=LOG_PREFIX,
        )

    async def mark_missed(self, now: datetime) -> int:
        """當日結束仍未到診者標記為 missed，回傳筆數。不看 `enabled`：狀態記的
        是「當天沒有人回報到診」這件事實，與要不要推播無關。"""
        result = await self._col.update_many(
            {"status": {"$in": list(OPEN_STATUSES)}, "day_end_at": {"$lte": now}},
            {"$set": {"status": "missed", "updated_at": now}},
        )
        return result.modified_count
