"""索引服務的每日排程外殼。

與推播排程分開的是**排程**，不只是服務：兩者的心跳各自登記，否則索引停擺會被
推播的心跳掩蓋——推播照跑、外觀健康，只是內容永遠停在停擺那一天
（design.md 決策 2）。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, Optional

from app.core import scheduler_heartbeat
from app.models.medication import TAIPEI_TZ

logger = logging.getLogger(__name__)


class DrugNewsIndexScheduler:
    HEARTBEAT_NAME = "medical_news_index"
    HEARTBEAT_INTERVAL_SECONDS = 24 * 60 * 60

    def __init__(self, *, index_service: Any, run_time: str) -> None:
        self._index_service = index_service
        self._run_time = run_time
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        scheduler_heartbeat.register(
            self.HEARTBEAT_NAME,
            expected_interval_seconds=self.HEARTBEAT_INTERVAL_SECONDS,
            tolerance_factor=1.5,
        )
        self._task = asyncio.create_task(self._run_loop())
        logger.info("[DrugNewsIndexScheduler] started, run_time=%s", self._run_time)

    async def stop(self) -> None:
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        logger.info("[DrugNewsIndexScheduler] stopped")

    async def _run_loop(self) -> None:
        while True:
            now = datetime.now(TAIPEI_TZ)
            next_run = self._next_run_at(now)
            await asyncio.sleep(max(0.0, (next_run - now).total_seconds()))
            scheduler_heartbeat.beat(self.HEARTBEAT_NAME)
            try:
                await self._index_service.run_once(
                    datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
                )
            except Exception:
                # 整輪失敗只記 log。推播排程照常拿昨天的索引跑——這正是兩者
                # 分開的理由，政府站台逾時不該讓使用者今天收不到東西。
                logger.exception("[DrugNewsIndexScheduler] 索引失敗")

    def _next_run_at(self, now: datetime) -> datetime:
        hour, minute = (int(part) for part in self._run_time.split(":"))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate


def start_drug_news_index_scheduler(
    *, enabled: bool = True, index_service: Any, run_time: str
) -> Optional[DrugNewsIndexScheduler]:
    if not enabled or index_service is None:
        logger.info("[DrugNewsIndexScheduler] disabled")
        return None

    scheduler = DrugNewsIndexScheduler(index_service=index_service, run_time=run_time)
    scheduler.start()
    return scheduler
