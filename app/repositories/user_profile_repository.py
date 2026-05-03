"""這個檔案負責與 MongoDB 進行互動，提供使用者資料的增刪改查功能。
用upsert避免重複插入,並且在更新或
插入資料時會自動添加時間戳記。
"""

import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from app.db.mongodb import MongoDBManager

logger = logging.getLogger(__name__)


class UserProfileRepository:
    @staticmethod
    async def upsert_user_profile(line_id: str, payload: Dict[str, Any]) -> bool:
        col = MongoDBManager.get_users_collection()
        now = datetime.now(tz=timezone.utc)
        result = await col.update_one(
            {"line_id": line_id},
            {
                "$set": {**payload, "line_id": line_id, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        return result.matched_count > 0 or result.upserted_id is not None

    @staticmethod
    async def get_user_profile(line_id: str) -> Optional[Dict[str, Any]]:
        col = MongoDBManager.get_users_collection()
        return await col.find_one({"line_id": line_id})
