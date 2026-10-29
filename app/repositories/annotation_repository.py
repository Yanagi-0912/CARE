from datetime import datetime, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db.mongodb import MongoDBManager


class AnnotationRepository:
    """一個帳號、題組版本一份進度；Mongo 的 _id 唯一索引負責隔離。"""

    def __init__(self, collection: Any = None):
        self._collection = collection

    @property
    def collection(self):
        if self._collection is not None:
            return self._collection
        return MongoDBManager.get_database()["annotation_sessions"]

    async def get_or_create(self, key: str, user_id: str, task: str, fingerprint: str, order: list[dict]):
        initial = {
            "line_user_id": user_id,
            "task": task,
            "fingerprint": fingerprint,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "revision": 0,
            "position": 0,
            "answers": {},
            "order": order,
        }
        try:
            return await self.collection.find_one_and_update(
                {"_id": key}, {"$setOnInsert": initial},
                upsert=True, return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # 同一帳號首次同時開兩個視窗：採用已建立的那份，不重設進度。
            return await self.collection.find_one({"_id": key})

    async def save(self, key: str, revision: int, position: int, answer: dict):
        return await self.collection.find_one_and_update(
            {"_id": key, "revision": revision},
            {
                "$set": {
                    f"answers.{position}": answer,
                    "position": position + 1,
                    "updated_at": answer["at"],
                },
                "$inc": {"revision": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
