# 負責將諮詢對話訊息存入 Redis，並提供查詢接口給 ConsultationService 使用。
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any, Protocol

from app.db.redis import RedisManager
from app.models.consultation import ConsultationMessage


class ConsultationStore(Protocol):
    """定義諮詢對話訊息存取的抽象介面，
    讓 `ConsultationService` 能依賴抽象介面而非具體實作，
    方便後續替換或 mock。
    """

    async def append_message(
        self, line_id: str, summary_date: date, message: ConsultationMessage
    ) -> None: ...

    async def list_messages(
        self, line_id: str, summary_date: date
    ) -> list[ConsultationMessage]: ...

    async def list_dates(self, line_id: str) -> list[date]: ...

    async def list_line_ids_by_date(self, summary_date: date) -> list[str]: ...


class RedisConsultationStore:
    """Redis 為主的諮詢對話儲存實作

    責任：
    - 將使用者訊息和 AI 回覆存入 Redis list，以日期分組
    - 支援依日期查詢對話列表
    - 自動設定 1 天 TTL，過期自動清除

    Key 格式：consultationRecord:{line_id}:{date}
    Value：JSON 陣列，每筆元素是一個 ConsultationMessage
    """

    def __init__(self, client: Any) -> None:
        # 初始化 Redis 客戶端。
        self._client = client

    async def append_message(
        self, line_id: str, summary_date: date, message: ConsultationMessage
    ) -> None:
        """把單一訊息（使用者或 AI 回覆）附加到 Redis list

        實作細節：
        - 序列化訊息成 JSON 字串，用 rpush 加到 list 尾端
        - 設定 1 天 TTL，自動過期清除
        """
        import logging

        logger = logging.getLogger(__name__)
        key = self._build_key(line_id, summary_date)
        payload = message.model_dump(mode="json")
        await self._client.rpush(key, json.dumps(payload, ensure_ascii=False))
        # 設定 TTL 為 1 天
        await self._client.expire(key, int(timedelta(days=1).total_seconds()))
        import logging

        logger = logging.getLogger(__name__)
        logger.info(
            f"[RedisConsultationStore] 成功寫入 Redis，key={key}, message_type={message.message_type}"
        )

    async def list_messages(
        self, line_id: str, summary_date: date
    ) -> list[ConsultationMessage]:
        """取出指定日期該使用者的所有對話訊息

        實作細節：
        - 用 lrange 取 Redis list 全部元素（0 到 -1）
        - 逐筆 JSON 反序列化成 ConsultationMessage
        """
        key = self._build_key(line_id, summary_date)
        raw_items = await self._client.lrange(key, 0, -1)
        messages: list[ConsultationMessage] = []
        for raw_item in raw_items:
            if isinstance(raw_item, bytes):
                raw_item = raw_item.decode("utf-8")
            messages.append(ConsultationMessage.model_validate(json.loads(raw_item)))
        return messages

    async def list_dates(self, line_id: str) -> list[date]:
        """取出該使用者在 Redis 內有對話記錄的所有日期。

        實作細節：
        - 用 keys() 掃出所有 consultationRecord:{line_id}:* 的 key
        - 從 key 尾端解析出日期字串，轉成 date 物件
        """
        key_pattern = f"consultationRecord:{line_id}:*"
        keys = await self._client.keys(key_pattern)
        dates: list[date] = []
        for key in keys:
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            date_text = key.rsplit(":", 1)[-1]
            try:
                dates.append(date.fromisoformat(date_text))
            except ValueError:
                continue
        return sorted(set(dates))

    async def list_line_ids_by_date(self, summary_date: date) -> list[str]:
        # 取出指定日期在 Redis 內有對話記錄的所有 line_id，目前每天自動摘要的 scheduler
        # 會先用它從 Redis 找出今天有對話的使用者，再逐一呼叫摘要，避免對沒有對話的人跑摘要。
        date_text = summary_date.isoformat()
        key_pattern = f"consultationRecord:*:{date_text}"
        keys = await self._client.keys(key_pattern)
        line_ids: set[str] = set()
        prefix = "consultationRecord:"
        suffix = f":{date_text}"
        for key in keys:
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            if not key.startswith(prefix) or not key.endswith(suffix):
                continue
            line_id = key[len(prefix) : -len(suffix)]
            if line_id:
                line_ids.add(line_id)
        return sorted(line_ids)

    @staticmethod
    def _build_key(line_id: str, summary_date: date) -> str:
        # 組合 Redis key，格式為 consultationRecord:{line_id}:{date}
        # redis用 ":" 分隔不同層級的資訊，下方將最終context存在consultationRecord
        # 下的line_id層下的summary_date層內
        return f"consultationRecord:{line_id}:{summary_date.isoformat()}"


def build_consultation_store() -> RedisConsultationStore:
    # 建立 Redis 諮詢對話存放器
    return RedisConsultationStore(RedisManager.get_client())
