from __future__ import annotations

import logging
from typing import Optional

import redis.asyncio as redis_async  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)


class RedisManager:
    _client: Optional[redis_async.Redis] = None
    _redis_url: str = ""

    @classmethod
    def configure(cls, redis_url: str) -> None:
        cls._redis_url = redis_url or ""

    @classmethod
    def get_client(cls) -> redis_async.Redis:
        if cls._client is None:
            redis_url = cls._redis_url.strip()
            if not redis_url:
                raise ValueError(
                    "未設定 Redis 連線字串，請先呼叫 RedisManager.configure()"
                )
            logger.info("Initializing async Redis connection...")
            cls._client = redis_async.from_url(redis_url, decode_responses=False)
        return cls._client
