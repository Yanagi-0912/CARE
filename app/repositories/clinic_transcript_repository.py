"""看診錄音紀錄（clinic_visit_records）的資料庫操作。

過期交給 Mongo 的 TTL 索引，跟對話原文同一個做法：`expires_at` 到期那一刻整筆消失，
不需要任何排程去掃。少一個會忘記跑、跑失敗也沒人發現的背景工作。

音檔從來沒有進過這裡（見 `app/models/clinic_transcript.py`），所以「刪乾淨了沒」
不必跨儲存體去追——文字沒了就是沒了。
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from bson import ObjectId
from bson.errors import InvalidId

from app.db.mongodb import MongoDBManager
from app.models.clinic_transcript import ClinicVisitRecord

logger = logging.getLogger(__name__)

LOG_PREFIX = "[ClinicTranscriptRepository]"


def _collection(collection: Optional[Any] = None) -> Any:
    if collection is not None:
        return collection
    return MongoDBManager.get_database()["clinic_visit_records"]


def _to_record(document: dict) -> ClinicVisitRecord:
    document = dict(document)
    document["_id"] = str(document["_id"])
    return ClinicVisitRecord.model_validate(document)


class ClinicTranscriptRepository:
    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        col = _collection(collection)
        # expireAfterSeconds=0 表示以 expires_at 的時刻為準過期。
        await col.create_index(
            [("expires_at", 1)],
            name="clinic_visit_records_expires_at_ttl",
            expireAfterSeconds=0,
        )
        # 清單一律是「某位就診者、由新到舊」。
        await col.create_index(
            [("user_id", 1), ("recorded_at", -1)],
            name="clinic_visit_records_user_recorded_at",
        )

    @staticmethod
    async def create(
        record: ClinicVisitRecord, collection: Optional[Any] = None
    ) -> ClinicVisitRecord:
        col = _collection(collection)
        document = record.model_dump(by_alias=True, exclude={"id"})
        result = await col.insert_one(document)
        logger.info("%s 已建立紀錄 %s", LOG_PREFIX, result.inserted_id)
        return record.model_copy(update={"id": str(result.inserted_id)})

    @staticmethod
    async def list_for_user(
        user_id: str,
        limit: int = 20,
        collection: Optional[Any] = None,
    ) -> List[ClinicVisitRecord]:
        col = _collection(collection)
        cursor = col.find({"user_id": user_id}).sort("recorded_at", -1).limit(limit)
        return [_to_record(document) async for document in cursor]

    @staticmethod
    async def get(
        record_id: str,
        user_id: str,
        collection: Optional[Any] = None,
    ) -> Optional[ClinicVisitRecord]:
        """查一筆。

        `user_id` 一起進條件，不是查完再比對：少一條「查到了但忘記檢查擁有者」的
        路徑。查不到與不屬於這位使用者，對呼叫端是同一件事（都回 None），
        否則這支會變成探測他人 record_id 是否存在的管道。
        """
        try:
            object_id = ObjectId(record_id)
        except (InvalidId, TypeError):
            return None
        document = await _collection(collection).find_one(
            {"_id": object_id, "user_id": user_id}
        )
        return _to_record(document) if document else None

    @staticmethod
    async def delete(
        record_id: str,
        user_id: str,
        collection: Optional[Any] = None,
    ) -> bool:
        try:
            object_id = ObjectId(record_id)
        except (InvalidId, TypeError):
            return False
        result = await _collection(collection).delete_one(
            {"_id": object_id, "user_id": user_id}
        )
        return result.deleted_count > 0

    @staticmethod
    async def mark_ready(
        record_id: str,
        *,
        segments: list,
        summary: dict,
        drug_hints: list,
        speaker_count: int,
        collection: Optional[Any] = None,
    ) -> None:
        """背景轉錄成功後把結果補上去。

        不帶 user_id 條件：這支只由背景工作用自己剛建立的 record_id 呼叫，
        不經過任何使用者輸入。
        """
        await _collection(collection).update_one(
            {"_id": ObjectId(record_id)},
            {
                "$set": {
                    "status": "ready",
                    "segments": segments,
                    "summary": summary,
                    "drug_hints": drug_hints,
                    "speaker_count": speaker_count,
                }
            },
        )

    @staticmethod
    async def mark_failed(
        record_id: str, reason: str, collection: Optional[Any] = None
    ) -> None:
        """轉錄失敗。留下紀錄而不是刪掉，否則使用者只會看到錄音憑空消失。"""
        await _collection(collection).update_one(
            {"_id": ObjectId(record_id)},
            {"$set": {"status": "failed", "failure_reason": reason}},
        )
