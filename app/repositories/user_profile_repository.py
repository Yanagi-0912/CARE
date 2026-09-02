"""這個檔案負責與 MongoDB 進行互動，提供使用者資料的增刪改查功能。
用upsert避免重複插入,並且在更新或
插入資料時會自動添加時間戳記。
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from app.db.mongodb import MongoDBManager

logger = logging.getLogger(__name__)


class UserProfileRepository:
    @staticmethod
    async def upsert_user_profile(line_id: str, payload: Dict[str, Any]) -> bool:
        col = MongoDBManager.get_users_collection()
        now = datetime.now(tz=timezone.utc)
        set_fields = {**payload, "line_id": line_id, "updated_at": now}
        # role 僅在首次插入時設定，避免登入 upsert 覆寫 admin
        set_fields.pop("role", None)
        result = await col.update_one(
            {"line_id": line_id},
            {
                "$set": set_fields,
                "$setOnInsert": {"created_at": now, "role": "user"},
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
    async def list_all_line_ids(collection: Optional[Any] = None) -> List[str]:
        """全體使用者的 line_id。

        每日消息卡的收件人是**全體**使用者，不是「有用藥的那批」——Tier 2 保底
        存在的理由正是讓沒有用藥資料的人也每天收得到東西。

        只投影 `line_id`：使用者文件含健康欄位，為了取一個 id 而把整份文件撈進
        記憶體是不必要的暴露，資料量大時也是不必要的傳輸。

        本方法帶 `collection` 參數（本檔既有方法沒有），比照
        `medication_repository.py` 的慣例，讓測試以依賴注入傳入替身而不必
        monkey patch。
        """
        if collection is None:
            collection = MongoDBManager.get_users_collection()

        cursor = collection.find({}, {"line_id": 1})
        docs = await cursor.to_list(length=None)
        return [doc["line_id"] for doc in docs if doc.get("line_id")]

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
                    "settings.voice_reply_enabled": enabled,
                    "updated_at": now,
                }
            },
        )
        return result.matched_count > 0
