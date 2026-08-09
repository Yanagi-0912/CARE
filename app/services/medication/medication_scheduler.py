import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Awaitable, Callable, Optional

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
    MedicationRepository,
)
from app.services.line_messaging.flex.medication_flex import (
    build_caregiver_alert_flex,
    build_caregiver_missed_summary_flex,
    build_patient_medication_flex,
    build_patient_urgent_reminder_flex,
)
from app.services.line_messaging.reply.reply import LineReplier
from app.services.users.user_profile_service import UserProfileService

logger = logging.getLogger(__name__)

# 錯過多久之後就不再補推播。對應 APScheduler 的 misfire_grace_time。
# 預設取 20 分鐘（＝T+20 催促的門檻）：短暫部署造成的延遲仍會正常送達，
# 超過這個範圍代表整條 T+0／T+20／T+30 時序已經失去意義，補推只會變成連環轟炸。
DEFAULT_MISFIRE_GRACE_MINUTES = 20


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
        misfire_grace_minutes: int = DEFAULT_MISFIRE_GRACE_MINUTES,
    ) -> None:
        self._replier = replier
        self._user_profile_service = user_profile_service
        self._check_interval_seconds = check_interval_seconds
        self._misfire_grace_minutes = misfire_grace_minutes
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

    async def _dispatch(
        self,
        *,
        stage: str,
        log_id: str,
        claim: Callable[[str], Awaitable[bool]],
        release: Callable[[str], Awaitable[bool]],
        send: Callable[[], Awaitable[bool]],
    ) -> None:
        """
        推播權搶佔 → 推播 → 失敗還原。

        三個階段共用同一套流程，差別只在旗標與訊息內容。搶佔的理由見
        `MedicationLogRepository` 的「推播權搶佔」段落：查詢與標記之間沒有原子性，
        多實例並存時會重複推播。
        """
        try:
            claimed = await claim(log_id)
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to claim %s for log %s", stage, log_id
            )
            return

        if not claimed:
            # 旗標已被其他實例搶走，或先前的 tick 已送出。
            return

        try:
            sent = await send()
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to process %s for log %s", stage, log_id
            )
            sent = False

        if not sent:
            # 推播沒成功就把推播權還回去，下一個 tick 會重新搶佔並重試。
            with suppress(Exception):
                await release(log_id)

    async def _resolve_medication_names(
        self,
        reminder_id: str,
        scheduled_at: datetime,
        *,
        reminder_collection: Optional[Any] = None,
        medication_collection: Optional[Any] = None,
    ) -> list[str]:
        """組裝推播文案時才解析藥名，且僅在這裡解析。

        刻意不放進 `process_ticks` 的展開／搶佔路徑：`MedicationLog` 本身不帶
        `medication_ids`，這裡另外用 `reminder_id` 查一次規則、再用
        `find_active_by_ids` 濾出當下仍有效的藥品——展開判定完全不會經過這段
        邏輯，即使查詢失敗也只影響這一則推播的藥品清單，不影響它是否送出。

        `reminder_collection`／`medication_collection` 僅供測試注入假的
        collection；正式路徑一律傳 None，落回 MongoDBManager 的真實連線。
        """
        try:
            reminder = await MedicationReminderRepository.get_reminder_by_id(
                reminder_id, collection=reminder_collection
            )
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to load reminder %s for medication list",
                reminder_id,
            )
            return []

        if not reminder or not reminder.medication_ids:
            return []

        # 「當下有效」用推播當下、而非規則建立當下的台北日期判斷，貼近使用者
        # 實際看到這則推播時的療程狀態。
        date_str = ensure_aware_utc(scheduled_at).astimezone(TAIPEI_TZ).strftime(
            "%Y-%m-%d"
        )
        try:
            medications = await MedicationRepository.find_active_by_ids(
                reminder.medication_ids, date_str, collection=medication_collection
            )
        except Exception:
            logger.exception(
                "[MedicationScheduler] Failed to resolve medications for reminder %s",
                reminder_id,
            )
            return []
        return [medication.name for medication in medications]

    async def _send_patient_reminder(self, log: MedicationLog) -> bool:
        language, font_size = await self._resolve_display_prefs(log.user_id)
        medication_names = await self._resolve_medication_names(
            log.reminder_id, log.scheduled_at
        )
        flex_msg = build_patient_medication_flex(
            log_id=log.id,
            slot_type=log.slot_type,
            scheduled_time=to_taipei_hm(log.scheduled_at, default="08:00"),
            disabled=False,
            medication_names=medication_names,
            language=language,
            font_size=font_size,
        )
        return await self._replier.push_flex(log.user_id, flex_msg)

    async def _send_urgent_reminder(self, log: MedicationLog) -> bool:
        language, font_size = await self._resolve_display_prefs(log.user_id)
        medication_names = await self._resolve_medication_names(
            log.reminder_id, log.scheduled_at
        )
        urgent_flex = build_patient_urgent_reminder_flex(
            log_id=log.id,
            slot_type=log.slot_type,
            scheduled_time=to_taipei_hm(log.scheduled_at, default="08:00"),
            medication_names=medication_names,
            language=language,
            font_size=font_size,
        )
        return await self._replier.push_flex(log.user_id, urgent_flex)

    async def _resolve_patient_name(self, user_id: str) -> str:
        """取得用藥者的顯示名稱；查不到時回退為泛稱。"""
        if not self._user_profile_service:
            return "成員"
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
            if profile and isinstance(profile, dict) and profile.get("name"):
                return profile["name"]
        except Exception:
            pass
        return "成員"

    async def _send_caregiver_alert(self, log: MedicationLog) -> bool:
        patient_name = await self._resolve_patient_name(log.user_id)

        # 這則推播的收件人是家屬，語言與字級需取家屬本人的設定
        language, font_size = await self._resolve_display_prefs(
            log.alert_notify_user_id
        )
        alert_flex = build_caregiver_alert_flex(
            patient_name=patient_name,
            slot_type=log.slot_type,
            scheduled_time=to_taipei_hm(log.scheduled_at, default="08:00"),
            language=language,
            font_size=font_size,
        )
        return await self._replier.push_flex(log.alert_notify_user_id, alert_flex)

    async def _notify_missed_summary(
        self, misfired_by_caregiver: dict[str, list[MedicationLog]]
    ) -> None:
        """
        把本次 tick 新發現的錯過時段，依家屬彙整成一則通知送出。

        不做推播權搶佔：來源是 `upsert_log` 回報的 created 旗標，而 (reminder_id,
        scheduled_at) 唯一索引保證同一個時段只會被插入一次，所以多實例並存時也只有
        真正插入成功的那個實例會拿到這些 log。

        送不出去就只記 log，不重試：這是中斷後的補充告知，為它額外維護一份「待通知」
        狀態並不划算，而錯過的時段本身已經以 status=missed 留在資料庫裡。
        """
        name_cache: dict[str, str] = {}

        for caregiver_id, logs in misfired_by_caregiver.items():
            if not caregiver_id:
                continue
            try:
                entries: list[dict[str, str]] = []
                for log in sorted(logs, key=lambda item: ensure_aware_utc(item.scheduled_at)):
                    if log.user_id not in name_cache:
                        name_cache[log.user_id] = await self._resolve_patient_name(
                            log.user_id
                        )
                    entries.append(
                        {
                            "patient_name": name_cache[log.user_id],
                            "slot_type": log.slot_type,
                            "scheduled_time": to_taipei_hm(
                                log.scheduled_at, default="08:00"
                            ),
                        }
                    )

                language, font_size = await self._resolve_display_prefs(caregiver_id)
                await self._replier.push_flex(
                    caregiver_id,
                    build_caregiver_missed_summary_flex(
                        missed=entries, language=language, font_size=font_size
                    ),
                )
            except Exception:
                logger.exception(
                    "[MedicationScheduler] Failed to send missed-slot summary to %s",
                    caregiver_id,
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

        # 錯過超過 grace 的時段不再推播（見 DEFAULT_MISFIRE_GRACE_MINUTES）。
        misfire_cutoff = current_time - timedelta(minutes=self._misfire_grace_minutes)

        # ── 階段 1：T+0min 首刷提醒建立與推播 ──────────────────────────
        # 1. 查詢今日所有已到期 (scheduled_time <= current_hm_str) 的活躍提醒並為其 upsert 當日 log
        active_reminders = await MedicationReminderRepository.list_active_reminders_up_to_time(
            max_scheduled_time=current_hm_str, target_date_str=today_date_str
        )
        # 本次 tick 才發現的錯過時段，依通報家屬分組，稍後彙整成一則通知。
        misfired_by_caregiver: dict[str, list[MedicationLog]] = {}
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

                # 停機期間錯過的時段：仍建立 log 留下紀錄，但直接記為 missed 且三個
                # 旗標全部設起，不推播。上面的 created_at 檢查只擋得住「提醒是後來才
                # 建立的」，擋不住「服務當時沒在跑」——下午三點重啟時，早上 08:00 與
                # 中午 12:00 的 log 會在同一個 tick 內被建立，接著三個階段依序判定成立，
                # 使用者一次收到四則、家屬收到兩則逾時警報。
                is_misfired = scheduled_dt < misfire_cutoff
                if is_misfired:
                    logger.info(
                        "[MedicationScheduler] Misfired slot recorded without push: "
                        "reminder=%s scheduled=%s grace=%dmin",
                        reminder.id,
                        scheduled_dt.isoformat(),
                        self._misfire_grace_minutes,
                    )

                log_data = MedicationLog(
                    reminder_id=reminder.id,
                    user_id=reminder.user_id,
                    alert_notify_user_id=reminder.creator_user_id,
                    slot_type=reminder.slot_type,
                    scheduled_at=scheduled_dt,
                    timeout_at=timeout_dt,
                    status="missed" if is_misfired else "pending",
                    patient_reminder_sent=is_misfired,
                    urgent_reminder_sent=is_misfired,
                    caregiver_alert_sent=is_misfired,
                )
                # upsert 是 $setOnInsert，所以只有「第一次建立」會套用上面的靜默標記；
                # 正常運行中早就建好的 log 不會被這裡蓋掉。
                saved_log, created = await MedicationLogRepository.upsert_log(log_data)

                # 只有「本次才建立」的錯過時段要通知；否則每 60 秒的 tick 都會重算出
                # 同一批 is_misfired，家屬會被同一則通知洗版。
                if created and is_misfired:
                    misfired_by_caregiver.setdefault(
                        reminder.creator_user_id, []
                    ).append(saved_log)
            except Exception:
                logger.exception(
                    f"[MedicationScheduler] Failed to upsert T+0min log for user {reminder.user_id}"
                )

        # 1b. 中斷期間錯過的時段：每位家屬彙整成一則通知（措辭與 T+30 逾時警報不同）
        if misfired_by_caregiver:
            await self._notify_missed_summary(misfired_by_caregiver)

        # 2. 使用 list_pending_patient_reminders 查詢已到期 (scheduled_at <= current_time) 且未發送首刷的紀錄推播
        pending_initial_logs = await MedicationLogRepository.list_pending_patient_reminders(
            threshold_time=current_time
        )
        for log in pending_initial_logs:
            await self._dispatch(
                stage="T+0min initial reminder",
                log_id=log.id,
                claim=MedicationLogRepository.claim_patient_reminder,
                release=MedicationLogRepository.release_patient_reminder,
                send=partial(self._send_patient_reminder, log),
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
            await self._dispatch(
                stage="T+20min urgent reminder",
                log_id=log.id,
                claim=MedicationLogRepository.claim_patient_urgent_reminder,
                release=MedicationLogRepository.release_patient_urgent_reminder,
                send=partial(self._send_urgent_reminder, log),
            )

        # ── 階段 3：T+30min 第三次家屬逾時警報 ─────────────────────────
        # 門檻計算：找出 timeout_at <= 當前時間 且狀態仍為 pending (未確認用藥) 的記錄
        pending_alert_logs = await MedicationLogRepository.list_pending_caregiver_alerts(
            threshold_time=current_time
        )
        for log in pending_alert_logs:
            await self._dispatch(
                stage="T+30min caregiver alert",
                log_id=log.id,
                claim=MedicationLogRepository.claim_caregiver_alert,
                release=MedicationLogRepository.release_caregiver_alert,
                send=partial(self._send_caregiver_alert, log),
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
