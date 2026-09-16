"""對話原文的正式紀錄。

Redis 只是 agent 讀最近幾則的快取（TTL 一天、有長度上限，主機重啟也可能消失），
摘要與原始訊息查詢都以這份為準。每則訊息寫入後不再修改。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from app.db.mongodb import MongoDBManager
from app.models.chat_message import ChatMessage
from app.models.medication import ensure_aware_utc

# 保存政策（2026-09-14 決定）：原文保留 30 天，由 Mongo TTL 依 expires_at 清除，
# 應用端不需要排程刪除。刻意不做成環境變數——這是個資保存期限，改它要留紀錄。
# 注意只能安全地往短改：已刪除的訊息回不來，已寫入的訊息則沿用寫入時算出的到期時間。
RETENTION_DAYS = 30


class ConversationLogRepository:
    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        if collection is None:
            collection = MongoDBManager.get_conversation_messages_collection()

        # expireAfterSeconds=0 表示以 expires_at 的時刻為準過期
        await collection.create_index(
            [("expires_at", 1)],
            name="conversation_messages_expires_at_ttl",
            expireAfterSeconds=0,
        )
        await collection.create_index(
            [("line_id", 1), ("timestamp", 1)],
            name="conversation_messages_line_id_timestamp",
        )

    @staticmethod
    async def append_message(
        line_id: str, message: ChatMessage, collection: Optional[Any] = None
    ) -> None:
        if collection is None:
            collection = MongoDBManager.get_conversation_messages_collection()

        timestamp = ensure_aware_utc(message.timestamp)
        await collection.insert_one(
            {
                "line_id": line_id,
                "message_type": message.message_type,
                "content": message.content,
                "timestamp": timestamp,
                "expires_at": timestamp + timedelta(days=RETENTION_DAYS),
            }
        )

    @staticmethod
    def _timestamp_range(
        since: Optional[datetime], until: Optional[datetime]
    ) -> dict[str, Any]:
        """`[since, until)` 的 timestamp 條件；兩端都沒給就是空條件（全部）。"""
        bounds: dict[str, Any] = {}
        if since is not None:
            bounds["$gte"] = ensure_aware_utc(since)
        if until is not None:
            bounds["$lt"] = ensure_aware_utc(until)
        return {"timestamp": bounds} if bounds else {}

    @staticmethod
    async def list_messages(
        line_id: str,
        collection: Optional[Any] = None,
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> list[ChatMessage]:
        """這位使用者的訊息，依時間遞增。

        `since`／`until` 是 UTC 的半開區間 `[since, until)`，給每日摘要排程用：
        它只需要昨天那一天，卻曾經把每個人 30 天的原文全撈回來再在 Python 裡
        過濾——資料量是 O(使用者數 × 30 天)。不帶的話仍是全部（LIFF 原始紀錄頁
        與手動摘要走這條）。
        """
        if collection is None:
            collection = MongoDBManager.get_conversation_messages_collection()

        cursor = collection.find(
            {"line_id": line_id, **ConversationLogRepository._timestamp_range(since, until)},
            {"_id": 0, "expires_at": 0},
        ).sort("timestamp", 1)
        documents = await cursor.to_list(length=None)
        return [
            ChatMessage(
                line_id=document["line_id"],
                message_type=document["message_type"],
                content=document["content"],
                # Motor 未開 tz_aware，讀回的是 naive UTC；不補時區的話，
                # LIFF 會把 ISO 字串當成手機本地時間，顯示差 8 小時。
                timestamp=ensure_aware_utc(document["timestamp"]),
            )
            for document in documents
        ]

    @staticmethod
    async def list_line_ids(
        collection: Optional[Any] = None,
        *,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> list[str]:
        """有對話的使用者。帶 `since`／`until` 時只算那段期間內有訊息的人。

        不帶的話，超過保存期限的訊息已被 TTL 清掉，這裡就是「保存期內有對話
        的使用者」。每日摘要排程帶昨天的區間：沒在昨天講話的人不該被列出來，
        否則會為每個人各產一份「該日期尚無諮詢記錄」的摘要。
        """
        if collection is None:
            collection = MongoDBManager.get_conversation_messages_collection()
        query = ConversationLogRepository._timestamp_range(since, until)
        return sorted(await collection.distinct("line_id", query))
