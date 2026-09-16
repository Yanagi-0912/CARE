"""走失求救的排程：位置停止更新的通知、時間到自動結束。

判斷與推播都在 LostLocationService.process_due；這裡只負責每分鐘叫它一次與報
心跳。每分鐘一次的理由：停止更新的門檻是 3 分鐘（STALE_AFTER），檢查間隔再長，
家人最晚要等到 3 分鐘加上一整個間隔才知道。
"""

import logging
from datetime import datetime
from typing import Any, Optional

from app.services.scheduling.push_tick_scheduler import PushTickScheduler

logger = logging.getLogger(__name__)


class LostLocationScheduler(PushTickScheduler):
    HEARTBEAT_NAME = "lost_location"
    LOG_PREFIX = "[LostLocationScheduler]"

    def __init__(self, service: Any, check_interval_seconds: int = 60) -> None:
        super().__init__(replier=None, check_interval_seconds=check_interval_seconds)
        self._service = service

    async def process_ticks(self, now: Optional[datetime] = None) -> None:
        await self._service.process_due(now)


def start_lost_location_scheduler(*, service: Any) -> LostLocationScheduler:
    scheduler = LostLocationScheduler(service)
    scheduler.start()
    return scheduler
