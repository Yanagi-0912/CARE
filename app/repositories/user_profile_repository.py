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
    async def sync_line_profile(
        #每次登入liff 時候line 傳給liff的最新頭像picture_url 同步到mongodb
        line_id: str,
        *,
        picture_url: str | None = None,
    ) -> bool:
        """只更新 LINE profile 相關欄位，不動健康資料。"""
        fields: Dict[str, Any] = {}
        if picture_url is not None:
            fields["picture_url"] = picture_url
        if not fields:
            return False

        col = MongoDBManager.get_users_collection()
        now = datetime.now(tz=timezone.utc)
        fields["updated_at"] = now
        result = await col.update_one({"line_id": line_id}, {"$set": fields})
        return result.matched_count > 0

    @staticmethod
    async def update_user_settings(line_id: str, settings_fields: Dict[str, Any]) -> bool:
        """只更新 settings 底下指定的欄位，不影響健康資料或其他欄位。

        用 "settings.<key>" 這種點記法只 $set 有帶入的欄位，
        避免整包覆蓋掉使用者沒有要改動的其他設定。
        """
        if not settings_fields:
            return False

        col = MongoDBManager.get_users_collection()
        now = datetime.now(tz=timezone.utc)
        set_fields = {f"settings.{key}": value for key, value in settings_fields.items()}
        set_fields["updated_at"] = now
        result = await col.update_one({"line_id": line_id}, {"$set": set_fields})
        return result.matched_count > 0

    @staticmethod
    async def get_user_profile(line_id: str) -> Optional[Dict[str, Any]]:
        col = MongoDBManager.get_users_collection()
        profile = await col.find_one({"line_id": line_id})
        if profile:
            # 移除 MongoDB 的 _id 欄位以便 JSON 序列化
            profile.pop("_id", None)
        return profile

    @staticmethod
    async def update_voice_reply_enabled(line_id: str, enabled: bool) -> bool:
        col = MongoDBManager.get_users_collection()
        now = datetime.now(tz=timezone.utc)
        result = await col.update_one(
            {"line_id": line_id},
            {
                "$set": {
                    "voice_reply_enabled": enabled,
                    "updated_at": now,
                }
            },
        )
        return result.matched_count > 0
