# 負責將諮詢對話訊息存入 Redis，並提供查詢接口給 ConsultationService 使用。
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Protocol

from app.db.redis import RedisManager
from app.models.consultation import ConsultationMessage
import logging


class ConsultationStore(Protocol):
    """定義諮詢對話訊息存取的抽象介面，
    讓 `ConsultationService` 能依賴抽象介面而非具體實作，
    方便後續替換或 mock。
    """

    async def append_message(
        self, line_id: str, message: ConsultationMessage
    ) -> None: ...

    async def list_messages(self, line_id: str) -> list[ConsultationMessage]: ...

    async def list_line_ids_by_date(self, summary_date: date) -> list[str]: ...


class RedisConsultationStore:
    """Redis 為主的諮詢對話儲存實作

    責任：
    - 將使用者訊息和 AI 回覆存入 Redis list，以使用者分組
    - 支援依訊息時間戳回推日期做查詢
    - 自動設定 1 天 TTL，過期自動清除

    Key 格式：consultationRecord:{line_id}
    Value：JSON 陣列，每筆元素是一個 ConsultationMessage
    """

    def __init__(self, client: Any) -> None:
        # 初始化 Redis 客戶端。
        self._client = client

    async def append_message(self, line_id: str, message: ConsultationMessage) -> None:
        """把單一訊息（使用者或 AI 回覆）附加到 Redis list

        實作細節：
        - 序列化訊息成 JSON 字串，用 rpush 加到 list 尾端
        - 設定 1 天 TTL，自動過期清除
        """

        logger = logging.getLogger(__name__)
        key = self._build_key(line_id)
        payload = message.model_dump(mode="json")
        await self._client.rpush(key, json.dumps(payload, ensure_ascii=False))
        # 設定 TTL 為 1 天
        await self._client.expire(key, int(timedelta(days=1).total_seconds()))
        logger.info(
            f"[RedisConsultationStore] 成功寫入 Redis，key={key}, message_type={message.message_type}"
        )

    async def list_messages(self, line_id: str) -> list[ConsultationMessage]:
        """取出該使用者目前 Redis 內的所有對話訊息

        實作細節：
        - 用 lrange 取 Redis list 全部元素（0 到 -1）
        - 逐筆 JSON 反序列化成 ConsultationMessage
        """
        key = self._build_key(line_id)
        raw_items = await self._client.lrange(key, 0, -1)
        messages: list[ConsultationMessage] = []
        for raw_item in raw_items:
            if isinstance(raw_item, bytes):
                raw_item = raw_item.decode("utf-8")
            messages.append(ConsultationMessage.model_validate(json.loads(raw_item)))
        return messages

    async def list_line_ids_by_date(self, summary_date: date) -> list[str]:
        # 現在 Redis 只按 user 分組，這裡回傳目前仍在 TTL 內的所有 user key。
        # summary_date 先保留作為相容參數，實際上不再參與過濾。
        key_pattern = "consultationRecord:*"
        keys = await self._client.keys(key_pattern)
        line_ids: set[str] = set()
        prefix = "consultationRecord:"
        for key in keys:
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            if not key.startswith(prefix):
                continue
            line_id = key[len(prefix) :]
            if line_id:
                line_ids.add(line_id)
        return sorted(line_ids)

    @staticmethod
    def _build_key(line_id: str) -> str:
        # 組合 Redis key，格式為 consultationRecord:{line_id}
        return f"consultationRecord:{line_id}"


def build_consultation_store() -> RedisConsultationStore:
    # 建立 Redis 諮詢對話存放器
    return RedisConsultationStore(RedisManager.get_client())
