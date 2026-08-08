import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Optional

from app.core.user_font_size import (
    DEFAULT_USER_FONT_SIZE,
    normalize_user_font_size,
)
from app.core.user_language import DEFAULT_USER_LANGUAGE, normalize_user_language
from app.models.medication import (
    TAIPEI_TZ,
    MedicationLog,
    ensure_aware_utc,
    to_taipei_hm,
)
from app.repositories.medication_repository import (
    MedicationLogRepository,
    MedicationReminderRepository,
)
from app.services.line_messaging.flex.medication_flex import (
    build_caregiver_alert_flex,
    build_patient_medication_flex,
    build_patient_urgent_reminder_flex,
)
from app.services.line_messaging.reply.reply import LineReplier
from app.services.users.user_profile_service import UserProfileService

logger = logging.getLogger(__name__)


class MedicationScheduler:
    """
    雙階遞進定時排程引擎 (MedicationScheduler)
    1. T+0min  首刷提醒建立與推播
    2. T+20min 第二次溫馨催促推播 (若逾時未用藥)
    3. T+30min 第三次家屬逾時通報警報 (若仍未用藥)
    """

    def __init__(
        self,
        replier: LineReplier,
        user_profile_service: Optional[UserProfileService] = None,
        check_interval_seconds: int = 60,
    ) -> None:
        self._replier = replier
        self._user_profile_service = user_profile_service
        self._check_interval_seconds = check_interval_seconds
        self._task: Optional[asyncio.Task] = None

    async def _resolve_display_prefs(self, user_id: str) -> tuple[str, str]:
        """
        取得收件人的語言與字級設定。
        排程是背景工作，沒有 request context，因此每則推播都需按收件人各自解析。
        """
        if not self._user_profile_service or not user_id:
            return DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to load display prefs for user %s", user_id
            )
            return DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE

        settings = (profile or {}).get("settings") or {}
        return (
            normalize_user_language(settings.get("language")),
            normalize_user_font_size(settings.get("font_size")),
        )

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_loop())
        logger.info("[MedicationScheduler] Background scheduler started")

    async def stop(self) -> None:
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        logger.info("[MedicationScheduler] Background scheduler stopped")

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.process_ticks()
            except Exception:
                logger.exception("[MedicationScheduler] Error during tick execution")
            await asyncio.sleep(self._check_interval_seconds)

    async def process_ticks(self, now: Optional[datetime] = None) -> None:
        """執行一次排程檢查 (可代入特定的 now 時間供單元測試或實機測試)"""
        current_time = now or datetime.now(TAIPEI_TZ)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=TAIPEI_TZ)

        today_date_str = current_time.strftime("%Y-%m-%d")
        current_hm_str = current_time.strftime("%H:%M")

        # ── 階段 1：T+0min 首刷提醒建立與推播 ──────────────────────────
        # 1. 查詢今日所有已到期 (scheduled_time <= current_hm_str) 的活躍提醒並為其 upsert 當日 log
        active_reminders = await MedicationReminderRepository.list_active_reminders_up_to_time(
            max_scheduled_time=current_hm_str, target_date_str=today_date_str
        )
        for reminder in active_reminders:
            try:
                scheduled_dt = datetime.strptime(
                    f"{today_date_str} {reminder.scheduled_time}", "%Y-%m-%d %H:%M"
                ).replace(tzinfo=current_time.tzinfo)
                timeout_dt = scheduled_dt + timedelta(minutes=30)

                # 不為「提醒建立之前」的時段補建 log。
                # 否則 20:00 新增一筆早上 08:00 的提醒，會在同一個 tick 內連續
                # 觸發首刷提醒、T+20 催促、以及 T+30 家屬逾時警報（全是假的）。
                if scheduled_dt < ensure_aware_utc(reminder.created_at):
                    continue

                log_data = MedicationLog(
                    reminder_id=reminder.id,
                    user_id=reminder.user_id,
                    alert_notify_user_id=reminder.creator_user_id,
                    slot_type=reminder.slot_type,
                    scheduled_at=scheduled_dt,
                    timeout_at=timeout_dt,
                    status="pending",
                    patient_reminder_sent=False,
                    urgent_reminder_sent=False,
                    caregiver_alert_sent=False,
                )
                await MedicationLogRepository.upsert_log(log_data)
            except Exception:
                logger.exception(
                    f"[MedicationScheduler] Failed to upsert T+0min log for user {reminder.user_id}"
                )

        # 2. 使用 list_pending_patient_reminders 查詢已到期 (scheduled_at <= current_time) 且未發送首刷的紀錄推播
        pending_initial_logs = await MedicationLogRepository.list_pending_patient_reminders(
            threshold_time=current_time
        )
        for log in pending_initial_logs:
            try:
                scheduled_hm = to_taipei_hm(log.scheduled_at, default="08:00")
                language, font_size = await self._resolve_display_prefs(log.user_id)
                flex_msg = build_patient_medication_flex(
                    log_id=log.id,
                    slot_type=log.slot_type,
                    scheduled_time=scheduled_hm,
                    disabled=False,
                    language=language,
                    font_size=font_size,
                )
                ok = await self._replier.push_flex(log.user_id, flex_msg)
                if ok:
                    await MedicationLogRepository.mark_patient_reminder_sent(log.id)
            except Exception:
                logger.exception(
                    f"[MedicationScheduler] Failed to process T+0min initial reminder for log {log.id}"
                )


        # ── 階段 2：T+20min 第二次溫馨催促 ─────────────────────────────
        # 門檻計算：找出 scheduled_at <= (當前時間 - 20分鐘) 的記錄
        # 說明：採用 $lte (小於等於) 能有效容忍執行秒數偏差或伺服器重啟延遲，絕不漏發。
        #       配合 urgent_reminder_sent 標記與單一 Document 狀態變更，保證不會重複發送。
        urgent_threshold = current_time - timedelta(minutes=20)
        pending_urgent_logs = await MedicationLogRepository.list_pending_urgent_reminders(
            threshold_time=urgent_threshold
        )
        for log in pending_urgent_logs:
            try:
                scheduled_hm = to_taipei_hm(log.scheduled_at, default="08:00")
                language, font_size = await self._resolve_display_prefs(log.user_id)
                urgent_flex = build_patient_urgent_reminder_flex(
                    log_id=log.id,
                    slot_type=log.slot_type,
                    scheduled_time=scheduled_hm,
                    language=language,
                    font_size=font_size,
                )
                ok = await self._replier.push_flex(log.user_id, urgent_flex)
                if ok:
                    # 成功發送後立即標記 urgent_reminder_sent=True，確保下次 Tick 不會重複處理
                    await MedicationLogRepository.mark_patient_urgent_reminder_sent(log.id)
            except Exception:
                logger.exception(
                    f"[MedicationScheduler] Failed to process T+20min urgent reminder for log {log.id}"
                )

        # ── 階段 3：T+30min 第三次家屬逾時警報 ─────────────────────────
        # 門檻計算：找出 timeout_at <= 當前時間 且狀態仍為 pending (未確認用藥) 的記錄
        pending_alert_logs = await MedicationLogRepository.list_pending_caregiver_alerts(
            threshold_time=current_time
        )
        for log in pending_alert_logs:
            try:
                patient_name = "成員"
                if self._user_profile_service:
                    try:
                        profile = await self._user_profile_service.get_user_profile(log.user_id)
                        if profile and isinstance(profile, dict) and profile.get("name"):
                            patient_name = profile["name"]
                    except Exception:
                        pass

                scheduled_hm = to_taipei_hm(log.scheduled_at, default="08:00")
                # 這則推播的收件人是家屬，語言與字級需取家屬本人的設定
                language, font_size = await self._resolve_display_prefs(
                    log.alert_notify_user_id
                )
                alert_flex = build_caregiver_alert_flex(
                    patient_name=patient_name,
                    slot_type=log.slot_type,
                    scheduled_time=scheduled_hm,
                    language=language,
                    font_size=font_size,
                )
                ok = await self._replier.push_flex(log.alert_notify_user_id, alert_flex)
                if ok:
                    # 成功發送警報後將記錄更新為 caregiver_alert_sent=True 且 status='missed'
                    await MedicationLogRepository.mark_caregiver_alert_sent(log.id)
            except Exception:
                logger.exception(
                    f"[MedicationScheduler] Failed to process T+30min caregiver alert for log {log.id}"
                )



def start_medication_scheduler(
    *,
    enabled: bool = True,
    replier: LineReplier,
    user_profile_service: Optional[UserProfileService] = None,
) -> Optional[MedicationScheduler]:
    if not enabled:
        logger.info("[MedicationScheduler] disabled")
        return None

    scheduler = MedicationScheduler(
        replier=replier,
        user_profile_service=user_profile_service,
    )
    scheduler.start()
    return scheduler
