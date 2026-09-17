"""LINE webhook 事件的去重（同一個 webhookEventId 只處理一次）。

LINE 在我們沒有及時回 200 時會把同一批事件重送（`deliveryContext.isRedelivery`
為 true），事件 id 不變。webhook 現在是先回 200 再在背景處理（見
routers/line/webhook.py），重送理論上會變少，但 pod 滾動更新、LINE 端逾時
判定仍會發生；不去重的話同一句話會被 agent 答兩次、對話紀錄也存兩筆。

正式環境用 Redis 的 `SET NX EX`：backend 有多個 replica，各自的記憶體看不到
彼此。Redis 連不上時退回本行程的有界 dict——單一 replica 下仍擋得住重送，
多 replica 下至少擋掉落在同一個 pod 的那份；這比「Redis 一掛就整個 webhook
處理不了」好。退回時只記一次 warning，避免 Redis 停機期間每則事件刷一行。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_KEY_PREFIX = "line:webhook:event:"

# Redis 一次 SET 正常在毫秒級；連不上時 redis-py 預設沒有連線逾時，會等到
# OS 的 TCP 逾時（macOS／Linux 預設 75 秒上下），整批事件就跟著卡住。
# 1 秒足以涵蓋跨 pod 的抖動，超過就當 Redis 不可用。
REDIS_CLAIM_TIMEOUT_SECONDS = 1.0

# 本行程備援 dict 的上限。LINE 一個 channel 的訊息量在開發階段每天數百則，
# 4096 筆 × 10 分鐘的窗口綽綽有餘；超過就淘汰最舊的，記憶體有界。
LOCAL_CAPACITY = 4096


class LineEventDedup:
    """認領事件 id：第一次認領回 True（該處理），已被認領回 False（略過）。"""

    def __init__(
        self,
        get_redis_client: Optional[Callable[[], object]],
        *,
        ttl_seconds: int = 600,
        local_capacity: int = LOCAL_CAPACITY,
        redis_timeout_seconds: float = REDIS_CLAIM_TIMEOUT_SECONDS,
    ) -> None:
        self._get_redis_client = get_redis_client
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._local_capacity = max(1, int(local_capacity))
        self._redis_timeout_seconds = redis_timeout_seconds
        # event_id → 到期時間（monotonic 秒）。OrderedDict 讓淘汰最舊的是 O(1)。
        self._local: OrderedDict[str, float] = OrderedDict()
        self._redis_fallback_logged = False

    async def claim(self, event_id: Optional[str]) -> bool:
        # 沒有 id 的事件（舊版 SDK 的測試替身、LINE 未附）無從去重，照常處理：
        # 寧可偶爾答兩次，也不能把正常事件丟掉。
        if not event_id:
            return True

        claimed = await self._claim_in_redis(event_id)
        if claimed is not None:
            return claimed
        return self._claim_locally(event_id)

    async def _claim_in_redis(self, event_id: str) -> Optional[bool]:
        """Redis 可用時回 True／False；不可用回 None，交給本行程備援。"""
        if self._get_redis_client is None:
            return None
        try:
            client = self._get_redis_client()
            result = await asyncio.wait_for(
                client.set(
                    f"{_KEY_PREFIX}{event_id}", b"1", nx=True, ex=self._ttl_seconds
                ),
                timeout=self._redis_timeout_seconds,
            )
        except Exception:
            if not self._redis_fallback_logged:
                self._redis_fallback_logged = True
                logger.warning(
                    "Redis 無法認領 webhook 事件 id，改用本行程記憶去重（只記一次）",
                    exc_info=True,
                )
            return None
        # 之前退回本地過而現在恢復了：下一次再掛時要再記一次。
        self._redis_fallback_logged = False
        return bool(result)

    def _claim_locally(self, event_id: str) -> bool:
        now = time.monotonic()
        self._evict_expired(now)
        if event_id in self._local:
            return False
        self._local[event_id] = now + self._ttl_seconds
        while len(self._local) > self._local_capacity:
            self._local.popitem(last=False)
        return True

    def _evict_expired(self, now: float) -> None:
        # 最舊的在最前面；碰到第一個還沒過期的就停。
        while self._local:
            oldest_id, expires_at = next(iter(self._local.items()))
            if expires_at > now:
                break
            del self._local[oldest_id]

    @property
    def local_size(self) -> int:
        return len(self._local)


def event_identity(event) -> tuple[Optional[str], bool]:
    """取出 (webhook_event_id, is_redelivery)；SDK 物件與測試替身都不一定有這兩個欄位。"""
    event_id = getattr(event, "webhook_event_id", None)
    if not isinstance(event_id, str):
        event_id = None
    delivery = getattr(event, "delivery_context", None)
    is_redelivery = bool(getattr(delivery, "is_redelivery", False))
    return event_id, is_redelivery

