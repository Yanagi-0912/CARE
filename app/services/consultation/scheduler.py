# 這是一個每天定時執行的排程，負責把昨天（台北日期）有對話的使用者補上摘要
from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import suppress
from datetime import datetime, time, timedelta
from app.core import scheduler_heartbeat
from app.models.medication import TAIPEI_TZ
from app.services.consultation.consultation_service import (
    ConsultationService,
    taipei_day_utc_range,
)
from app.repositories.conversation_log_repository import ConversationLogRepository

logger = logging.getLogger(__name__)


def _user_tag(line_id: str) -> str:
    """log 用的使用者代號：LINE user id 是個資，不進 log；雜湊前 8 碼足以在同一
    份 log 裡對上同一個人，卻反解不回 id。"""
    return hashlib.blake2b((line_id or "").encode("utf-8"), digest_size=4).hexdigest()


# 心跳登記。本排程器每天只醒一次，因此預期間隔是一整天。它的心跳對偵測
# 「排程器 pod 掛掉」幫助不大（要等一天以上才會過期），登記它只是為了讓
# 健康檢查端點能一併揭露狀態，真正的 liveness 依據是用藥提醒那支。
HEARTBEAT_NAME = "consultation_daily_summary"
HEARTBEAT_INTERVAL_SECONDS = 24 * 60 * 60


class ConsultationDailySummaryScheduler:
    def __init__(
        self,
        *,
        consultation_service: ConsultationService,
        consultation_store: ConversationLogRepository,
        run_time: str,
    ) -> None:
        self._consultation_service = consultation_service
        self._consultation_store = consultation_store
        self._run_time = run_time
        # 在建構時就驗：設錯的話 task 會在第一次醒來前的 _next_run_at 拋 ValueError
        # 靜默死掉——started 的 log 印了、心跳登記了，外觀健康、永不摘要。
        self._parsed_run_time = self._parse_time(run_time)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        # 容忍倍數放寬到 1.5：本排程器每天才醒一次，用預設的 3 倍代表要停擺
        # 三天才會被發現，而 1.5 倍（36 小時）已足以容納執行時間與時區換日的
        # 誤差，又不至於遲鈍到毫無意義。
        scheduler_heartbeat.register(
            HEARTBEAT_NAME,
            expected_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
            tolerance_factor=1.5,
        )
        # 建立背景 task；呼叫端不需要等待它完成，排程會自己在 _run_loop 中循環。
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "[ConsultationDailySummaryScheduler] started, run_time=%s",
            self._run_time,
        )

    async def stop(self) -> None:
        if self._task is None or self._task.done():
            return
        # app shutdown 時取消 sleep 中或執行中的背景 task，並吞掉預期中的 CancelledError。
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        logger.info("[ConsultationDailySummaryScheduler] stopped")

    async def _run_loop(self) -> None:
        while True:
            now = datetime.now(TAIPEI_TZ)
            next_run = self._next_run_at(now)
            wait_seconds = max(0.0, (next_run - now).total_seconds())
            # 等到下一次排程時間；執行完 _run_once 後會回到 while True，繼續等待隔天。
            logger.info(
                "   [ConsultationDailySummaryScheduler] next_run=%s, wait_seconds=%.1f",
                next_run.isoformat(timespec="seconds"),
                wait_seconds,
            )
            await asyncio.sleep(wait_seconds)
            # 心跳在 _run_once 之前回報：要證明的是「迴圈醒過來了」，
            # 而不是「這次摘要有沒有成功」。
            scheduler_heartbeat.beat(HEARTBEAT_NAME)
            try:
                await self._run_once()
            except Exception:
                # 單日失敗（例如列使用者時資料庫抖動）只記 log，明天照常。少了這層
                # 的話例外會直接殺掉 task，而且是靜默的：沒有 log、心跳也只是逐漸
                # 過期，要等監控才發現摘要停了。
                logger.exception("[ConsultationDailySummaryScheduler] tick 失敗")

    def _next_run_at(self, now: datetime) -> datetime:
        # run_time 是台北時間。容器時區是 UTC，照容器時鐘解讀的話 02:00 會變成台北 10:00。
        now = now.astimezone(TAIPEI_TZ)
        today_target = datetime.combine(
            now.date(), self._parsed_run_time, tzinfo=TAIPEI_TZ
        )
        if today_target <= now:
            return today_target + timedelta(days=1)
        return today_target

    @staticmethod
    def _parse_time(value: str) -> time:
        """`HH:MM`（台北時間）；格式或範圍錯誤時拋 ValueError，訊息點名環境變數。"""
        parts = (value or "").strip().split(":")
        if len(parts) != 2 or not all(part.strip().isdigit() for part in parts):
            raise ValueError(
                "CONSULTATION_DAILY_SUMMARY_TIME 必須是 HH:MM（台北時間），"
                f"目前的值是 {value!r}"
            )
        hour, minute = (int(part) for part in parts)
        if hour == 24 and minute == 0:  # 特例：24:00 表示隔天的 00:00
            return time(hour=0, minute=0)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(
                "CONSULTATION_DAILY_SUMMARY_TIME 的時刻超出範圍（00:00–23:59），"
                f"目前的值是 {value!r}"
            )
        return time(hour=hour, minute=minute)

    async def _run_once(self) -> None:
        # 摘要的對象是**昨天**（台北日期）：排程在凌晨跑，昨天已經完整、今天才剛開始。
        # 只列昨天有講話的人，查詢也只帶昨天那段——不再為每個人撈 30 天的原文。
        target_date = datetime.now(TAIPEI_TZ).date() - timedelta(days=1)
        since, until = taipei_day_utc_range(target_date)
        line_ids = await self._consultation_store.list_line_ids(since=since, until=until)
        if not line_ids:
            logger.info(
                "[ConsultationDailySummaryScheduler] %s 沒有任何對話，不需要摘要",
                target_date.isoformat(),
            )
            return

        logger.info(
            "[ConsultationDailySummaryScheduler] summarizing %d user(s) for %s",
            len(line_ids),
            target_date.isoformat(),
        )

        for line_id in line_ids:
            try:
                await self._consultation_service.summarize_day(line_id, target_date)
            except Exception:
                logger.exception(
                    "[ConsultationDailySummaryScheduler] summarize failed, user=%s",
                    _user_tag(line_id),
                )


# 在main的lifespan中呼叫，yield前的code表示在app啟動時執行，yield後的code表示在app關閉時執行。
def start_consultation_daily_summary_scheduler(
    *,
    enabled: bool,
    run_time: str,
    consultation_service: ConsultationService,
    consultation_store: ConversationLogRepository,
) -> ConsultationDailySummaryScheduler | None:
    if not enabled:
        logger.info("[ConsultationDailySummaryScheduler] disabled")
        return None

    scheduler = ConsultationDailySummaryScheduler(
        consultation_service=consultation_service,
        consultation_store=consultation_store,
        run_time=run_time,
    )
    scheduler.start()
    return scheduler
