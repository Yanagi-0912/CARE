# 這是一個每天定時執行的排程，負責對當天有對話記錄的使用者進行摘要
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta

from app.services.consultation.consultation_service import ConsultationService
from app.services.consultation.store import ConsultationStore

logger = logging.getLogger(__name__)


class ConsultationDailySummaryScheduler:
    def __init__(
        self,
        *,
        consultation_service: ConsultationService,
        consultation_store: ConsultationStore,
        run_time: str,
    ) -> None:
        self._consultation_service = consultation_service
        self._consultation_store = consultation_store
        self._run_time = run_time
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_loop())
        logger.info("[ConsultationDailySummaryScheduler] started")

    async def _run_loop(self) -> None:
        while True:
            now = datetime.now()
            next_run = self._next_run_at(now)
            wait_seconds = max(0.0, (next_run - now).total_seconds())
            await asyncio.sleep(wait_seconds)
            await self._run_once()

    def _next_run_at(self, now: datetime) -> datetime:
        run_time = self._parse_time(self._run_time)
        today_target = datetime.combine(now.date(), run_time)
        if today_target <= now:
            return today_target + timedelta(days=1)
        return today_target

    @staticmethod
    def _parse_time(value: str) -> time:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if hour == 24 and minute == 0:  # 特例：24:00 表示隔天的 00:00
            return time(hour=0, minute=0)
        return time(hour=hour, minute=minute)

    async def _run_once(self) -> None:
        target_date = date.today()
        # 從 Redis 找出今天有對話記錄的 line_id，然後逐一呼叫 summarize_today_if_needed
        line_ids = await self._consultation_store.list_line_ids_by_date(target_date)
        if not line_ids:
            return

        for line_id in line_ids:
            try:
                await self._consultation_service.summarize_today_if_needed(line_id)
            except Exception:
                logger.exception(
                    "[ConsultationDailySummaryScheduler] summarize failed, line_id=%s",
                    line_id,
                )


def start_consultation_daily_summary_scheduler(
    *,
    enabled: bool,
    run_time: str,
    consultation_service: ConsultationService,
    consultation_store: ConsultationStore,
) -> None:
    if not enabled:
        logger.info("[ConsultationDailySummaryScheduler] disabled")
        return

    scheduler = ConsultationDailySummaryScheduler(
        consultation_service=consultation_service,
        consultation_store=consultation_store,
        run_time=run_time,
    )
    scheduler.start()
