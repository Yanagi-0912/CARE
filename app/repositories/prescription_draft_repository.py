"""辨識草稿的存取。

草稿刻意獨立於 medications：把辨識結果放進 medications 再用狀態欄位區分，
會讓每一個讀取藥品的地方都得記得過濾狀態，漏掉一處就會讓未經使用者確認的
辨識結果流進推播。
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.db.mongodb import MongoDBManager
from app.models.prescription import PrescriptionDraft

logger = logging.getLogger(__name__)


class PrescriptionDraftRepository:
    """prescription_drafts collection 的操作。"""

    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        if collection is None:
            collection = MongoDBManager.get_prescription_drafts_collection()
        await collection.create_index("draft_id", unique=True)
        # expireAfterSeconds=0 表示以 expires_at 的時刻為準過期，
        # 由資料庫自行清除，不需要應用端排程。
        await collection.create_index("expires_at", expireAfterSeconds=0)

    @staticmethod
    async def create(
        draft: PrescriptionDraft, collection: Optional[Any] = None
    ) -> PrescriptionDraft:
        if collection is None:
            collection = MongoDBManager.get_prescription_drafts_collection()
        document = draft.model_dump(by_alias=True)
        document.pop("_id", None)
        await collection.insert_one(document)
        return draft

    @staticmethod
    async def find_by_id_for_user(
        draft_id: str, user_id: str, collection: Optional[Any] = None
    ) -> Optional[PrescriptionDraft]:
        """以 draft_id 與建立者同時過濾。

        查無時一律回 None，不分別回報「不存在」與「不屬於你」——分別回報
        會讓這個端點成為確認他人草稿是否存在的探測管道。
        """
        if collection is None:
            collection = MongoDBManager.get_prescription_drafts_collection()
        document = await collection.find_one(
            {"draft_id": draft_id, "creator_user_id": user_id}
        )
        if not document:
            return None
        document.pop("_id", None)
        return PrescriptionDraft(**document)

    @staticmethod
    async def mark_committed(
        draft_id: str,
        user_id: str,
        medication_ids: list[str],
        collection: Optional[Any] = None,
    ) -> tuple[bool, list[str]]:
        """以條件式更新取得提交權，回傳 (是否取得, 該草稿的建立結果)。

        「查草稿 → 建立藥品 → 標記已提交」之間沒有原子性；使用者連點兩次
        或前端重送，兩邊都會查到未提交的草稿而各建立一次。以「committed_at
        仍為 None」為條件更新，只有一次能成功。
        """
        if collection is None:
            collection = MongoDBManager.get_prescription_drafts_collection()

        updated = await collection.find_one_and_update(
            {
                "draft_id": draft_id,
                "creator_user_id": user_id,
                "committed_at": None,
            },
            {
                "$set": {
                    "committed_at": datetime.now(timezone.utc),
                    "committed_medication_ids": medication_ids,
                }
            },
        )
        if updated is not None:
            return True, medication_ids

        # 沒取得提交權有兩種可能：已經提交過，或草稿已被 TTL 清除。
        # 前者要回傳既有結果（冪等），後者必須讓呼叫端知道沒有結果可回。
        existing = await collection.find_one(
            {"draft_id": draft_id, "creator_user_id": user_id}
        )
        if not existing:
            return False, []
        return False, list(existing.get("committed_medication_ids") or [])

    @staticmethod
    async def release_commit(
        draft_id: str,
        user_id: str,
        medication_ids: list[str],
        collection: Optional[Any] = None,
    ) -> bool:
        """取得提交權之後、實際寫入藥品或提醒之前若失敗，把提交權還給草稿。

        取得權與寫入之間沒有原子性：mark_committed 成功之後，建立藥品或
        連結提醒仍可能因為暫時性資料庫錯誤而拋出例外。若不還原，草稿會
        永遠停在「已提交」但底下的藥品其實從未寫入，之後每次重試都會拿
        mark_committed 回傳的舊 id 當成功回應，處方就這樣憑空消失、
        沒有任何補救路徑。

        比對條件用 committed_medication_ids 是不是「我方才寫入的那組 id」，
        而不是無條件把 committed_at 設回 None——這樣才是「只釋放自己取得
        的提交權」，呼應 mark_committed 用「committed_at 仍為 None」表達
        「只有一次能取得」的同一種寫法。
        """
        if collection is None:
            collection = MongoDBManager.get_prescription_drafts_collection()
        result = await collection.update_one(
            {
                "draft_id": draft_id,
                "creator_user_id": user_id,
                "committed_medication_ids": medication_ids,
            },
            {"$set": {"committed_at": None, "committed_medication_ids": []}},
        )
        return result.modified_count > 0
