from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Protocol

from app.db.redis import RedisManager
from app.models.chat_message import ChatMessage


class ChatHistoryRepository(Protocol):
    """
    定義對話訊息存取的抽象介面，
    讓上層服務能依賴抽象介面而非具體實作，
    方便後續替換或 mock。
    """

    async def append_message(
        self, line_id: str, message: ChatMessage
    ) -> None: ...

    async def list_messages(self, line_id: str) -> list[ChatMessage]: ...


class RedisChatHistoryRepository:
    """
    Redis 對話歷史快取：只給 agent 讀最近幾則當上下文。
    將使用者訊息和 AI 回覆存入 Redis list，以使用者分組，只保留最近
    max_messages 則，自動設定 1 天 TTL。對話原文的正式紀錄在 Mongo
    （ConversationLogRepository），摘要與原始訊息查詢都讀那份。
    """

    def __init__(self, client: Any, max_messages: int) -> None:
        self._client = client
        self._max_messages = max_messages

    async def append_message(self, line_id: str, message: ChatMessage) -> None:
        """
        把單一訊息（使用者或 AI 回覆）附加到 Redis list。
        序列化訊息成 JSON 字串，用 rpush 加到 list 尾端
        設定 1 天 TTL，自動過期清除
        """
        import logging

        logger = logging.getLogger(__name__)
        key = self._build_key(line_id)
        payload = message.model_dump(mode="json")
        await self._client.rpush(key, json.dumps(payload, ensure_ascii=False))
        # 只留 agent 會讀的最近幾則，天天聊天的人列表才不會無上限變長
        await self._client.ltrim(key, -self._max_messages, -1)
        # 設定 TTL 為 1 天
        await self._client.expire(key, int(timedelta(days=1).total_seconds()))
        logger.info(
            f"[RedisChatHistoryRepository] 成功寫入 Redis，key={key}, message_type={message.message_type}"
        )

    async def list_messages(self, line_id: str) -> list[ChatMessage]:
        """
        取出該使用者目前 Redis 內的所有對話訊息。
        用 lrange 取 Redis list 全部元素（0 到 -1）
        逐筆 JSON 反序列化成 ChatMessage
        """
        key = self._build_key(line_id)
        raw_items = await self._client.lrange(key, 0, -1)
        messages: list[ChatMessage] = []
        for raw_item in raw_items:
            if isinstance(raw_item, bytes):
                raw_item = raw_item.decode("utf-8")
            messages.append(ChatMessage.model_validate(json.loads(raw_item)))
        return messages

    @staticmethod
    def _build_key(line_id: str) -> str:
        # 組合 Redis key，格式為 consultationRecord:{line_id}
        return f"consultationRecord:{line_id}"


def build_chat_history_repository(max_messages: int) -> RedisChatHistoryRepository:
    # 建立 Redis 對話歷史快取
    return RedisChatHistoryRepository(RedisManager.get_client(), max_messages)
