"""掛號提醒（appointment_reminders）資料庫操作。

推播權搶佔的設計與 `MedicationLogRepository` 相同，理由也相同（見該檔的「推播權
搶佔」段落）：查清單 → 逐筆搶佔 → 推播之間沒有原子性，多實例並存時會重複推播；
而清單查出來的那一刻起就是過期資料，使用者隨時可能在空檔按下「我已到診」。
所以**每一支 claim 都把清單查詢用過的狀態條件再斷言一次**，不能只看旗標。

與用藥不同的一點：這裡是 instance 而非一組 staticmethod，collection 由建構子注入。
測試因此能直接餵假的 collection，不必 monkeypatch 掉 `MongoDBManager`。
"""

import logging
from datetime import datetime
from typing import Any, Callable, List, Optional

from bson import ObjectId
from pymongo import ReturnDocument

from app.db.mongodb import MongoDBManager
from app.models.appointment import (
    CAREGIVER_ALERT_DELAY,
    OPEN_STATUSES,
    PRE_REMINDER_LEAD,
    AppointmentReminder,
)
from app.repositories.push_claim import release_push_claim

logger = logging.getLogger(__name__)

LOG_PREFIX = "[AppointmentReminderRepository]"


def _from_doc(doc: dict) -> AppointmentReminder:
    return AppointmentReminder(**{**doc, "_id": str(doc["_id"])})


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
        appointment_at）、當日結束的掃描（status + day_end_at）。

        不需要唯一索引：每一筆提醒就是一份文件，搶佔是單一文件的原子更新，
        不存在用藥那種「同一個時段被展開兩次」的問題。
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

    # ── CRUD ──────────────────────────────────────────────────────────

    async def create(self, reminder: AppointmentReminder) -> AppointmentReminder:
        # 不用 exclude_none：nullable 欄位以 null 明確寫入，文件形狀固定。
        doc = reminder.model_dump(by_alias=True)
        if not doc.get("_id"):
            doc["_id"] = str(ObjectId())
        await self._col.insert_one(doc)
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

    async def update_fields(
        self, reminder_id: str, set_doc: dict
    ) -> Optional[AppointmentReminder]:
        """收到什麼就 `$set` 什麼，包含 None。哪些欄位允許 null 由服務層界定。"""
        result = await self._col.update_one({"_id": reminder_id}, {"$set": set_doc})
        if result.matched_count == 0:
            return None
        return await self.get_by_id(reminder_id)

    async def delete(self, reminder_id: str) -> bool:
        result = await self._col.delete_one({"_id": reminder_id})
        return result.deleted_count > 0

    # ── 狀態轉移 ──────────────────────────────────────────────────────
    #
    # 兩支都是「以來源狀態為條件的單一文件原子更新」：本人與家屬同時按下同一顆
    # 按鈕時，只有一邊會寫入，另一邊拿到 None，由服務層重讀後判斷是冪等（已經是
    # 目標狀態）還是衝突。

    async def mark_departed(
        self, reminder_id: str, *, by_user_id: str, at: datetime
    ) -> Optional[AppointmentReminder]:
        doc = await self._col.find_one_and_update(
            {"_id": reminder_id, "status": "scheduled"},
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
            {"_id": reminder_id, "status": {"$in": list(OPEN_STATUSES)}},
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
        return await self._list(
            {
                "enabled": True,
                "status": "scheduled",
                "pre_reminder_sent": False,
                "appointment_at": {"$lte": now + PRE_REMINDER_LEAD, "$gt": now},
            }
        )

    async def claim_pre_reminder(self, reminder_id: str, now: datetime) -> bool:
        """`status: scheduled` 不是多餘的：已經按了出發的人不該再收到「出發了嗎」。"""
        return await self._claim(
            {
                "_id": reminder_id,
                "enabled": True,
                "status": "scheduled",
                "pre_reminder_sent": False,
                "appointment_at": {"$gt": now},
            },
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
        return await self._list(
            {
                "enabled": True,
                "status": {"$in": list(OPEN_STATUSES)},
                "start_reminder_sent": False,
                "appointment_at": {"$lte": now, "$gt": now - CAREGIVER_ALERT_DELAY},
                "day_end_at": {"$gt": now},
            }
        )

    async def claim_start_reminder(self, reminder_id: str, now: datetime) -> bool:
        """`status` 條件擋的是「剛按完我已到診，又收到門診時間到了」。"""
        return await self._claim(
            {
                "_id": reminder_id,
                "enabled": True,
                "status": {"$in": list(OPEN_STATUSES)},
                "start_reminder_sent": False,
                "appointment_at": {"$gt": now - CAREGIVER_ALERT_DELAY},
            },
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
        return await self._list(
            {
                "enabled": True,
                "status": {"$in": list(OPEN_STATUSES)},
                "caregiver_alert_sent": False,
                "appointment_at": {"$lte": now - CAREGIVER_ALERT_DELAY},
                "day_end_at": {"$gt": now},
            }
        )

    async def claim_caregiver_alert(self, reminder_id: str, now: datetime) -> bool:
        """與用藥不同，搶下家屬警報**不改狀態**：錯過（missed）只在當日結束時
        標記，T+30 之後本人或家屬仍然可以回報到診。"""
        return await self._claim(
            {
                "_id": reminder_id,
                "enabled": True,
                "status": {"$in": list(OPEN_STATUSES)},
                "caregiver_alert_sent": False,
                "day_end_at": {"$gt": now},
            },
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
