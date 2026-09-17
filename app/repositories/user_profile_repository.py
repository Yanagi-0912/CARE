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
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        """`line_id` 的唯一索引。

        沒有它的話，兩個請求同時為同一位新使用者 upsert（LIFF 首次登入時前端會
        連打好幾支 API）會各插一筆，之後每一次 `find_one({"line_id"})` 拿到哪一筆
        看 Mongo 心情——使用者會看到自己的健康資料「時有時無」。

        既有資料若已有重複，索引會建不起來（DuplicateKeyError）。呼叫端
        （lifespan）要接住並留 log，不能讓兩個 pod 因此一起 CrashLoop；清重複用
        `scripts/dedupe_users_line_id.py`，清完下一次啟動就建得起來。
        """
        if collection is None:
            collection = MongoDBManager.get_users_collection()

        await collection.create_index(
            [("line_id", 1)], unique=True, name="users_line_id_unique"
        )

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
        # 去重：唯一索引建起來之前資料庫裡可能還有同一個 line_id 的多份文件，
        # 回傳重複的話同一位使用者會在同一輪被推播兩次。保留首次出現的順序。
        seen: set[str] = set()
        line_ids: List[str] = []
        for doc in docs:
            line_id = doc.get("line_id")
            if line_id and line_id not in seen:
                seen.add(line_id)
                line_ids.append(line_id)
        return line_ids

    @staticmethod
    async def get_user_profile(line_id: str) -> Optional[Dict[str, Any]]:
        col = MongoDBManager.get_users_collection()
        # 固定取最早建立的那一筆：唯一索引建起來之前若已有重複文件，不排序的
        # find_one 會在幾份之間跳來跳去，使用者看到的資料時有時無。排序鍵與
        # scripts/dedupe_users_line_id.py 的「保留最舊」一致，清完重複前後看到的
        # 是同一份。
        profile = await col.find_one(
            {"line_id": line_id}, sort=[("created_at", 1), ("_id", 1)]
        )
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

    # --- LINE 進站流程 ---

    @staticmethod
    async def set_following(
        line_id: str, following: bool, at: Optional[datetime] = None
    ) -> bool:
        """記錄這個人有沒有把官方帳號留在好友裡（FollowEvent／UnfollowEvent）。

        欄位：`is_following`（bool）與 `unfollowed_at`（封鎖時間；重新加回時清成
        None）。沒有這個欄位的舊文件視為仍在追蹤——只有 unfollow 過的人才會被寫成
        False。只更新既有 profile、不 upsert：從沒開過 LIFF 的人沒有文件，排程器本來
        就不會推播給他，不必為了旗標憑空建一筆。
        回傳有沒有更新到文件（沒有 profile 時 False）。
        """
        col = MongoDBManager.get_users_collection()
        now = datetime.now(tz=timezone.utc)
        result = await col.update_one(
            {"line_id": line_id},
            {
                "$set": {
                    "is_following": following,
                    "unfollowed_at": None if following else (at or now),
                    "updated_at": now,
                }
            },
        )
        return result.matched_count > 0
