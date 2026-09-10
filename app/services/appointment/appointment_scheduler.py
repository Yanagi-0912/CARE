"""掛號提醒的推播排程。

| 時機 | 對象 | 內容 |
|---|---|---|
| T-1h | 本人＋家屬 | 「我已出發」卡片 |
| T+0，已出發 | 本人＋家屬 | 「我已到診」卡片 |
| T+0，未出發 | 本人＋家屬 | 催促卡片（附「我已到診」「我已出發」） |
| T+30，仍未到診 | 家屬 | 家屬警報 |
| 當日結束，仍未到診 | — | 標記 missed，不推播 |

沿用用藥提醒的排程骨架（`PushTickScheduler`）：迴圈、心跳、推播權搶佔、失敗還原、
收件人通知開關都是同一份實作。這裡只有「每個階段推什麼、推給誰」。

「家屬」只有一個來源：`FamilyAuthorizationService.notification_recipients`，種類
`appointment_reminder`。T-1h、T+0、T+30 三個階段都呼叫同一個函式，名單不會有兩套。
"""

import logging
from datetime import datetime, timezone
from functools import partial
from typing import Any, Awaitable, Callable, List, Optional

from app.i18n import t
from app.models.appointment import AppointmentReminder
from app.services.line_messaging.flex.appointment_flex import (
    build_caregiver_alert_flex,
    build_pre_reminder_flex,
    build_start_reminder_flex,
    format_hm,
    format_when,
)
from app.services.scheduling.push_tick_scheduler import PushTickScheduler

logger = logging.getLogger(__name__)

HEARTBEAT_NAME = "appointment"
NOTIFICATION_KIND = "appointment_reminder"

# (家屬看到的就診者名稱；本人收到時為 None, 語言, 字級) -> FlexMessage
_CardBuilder = Callable[[Optional[str], str, str], Any]


class AppointmentScheduler(PushTickScheduler):
    HEARTBEAT_NAME = HEARTBEAT_NAME
    LOG_PREFIX = "[AppointmentScheduler]"

    def __init__(
        self,
        replier: Any,
        repository: Any,
        authorization_service: Any = None,
        user_profile_service: Any = None,
        check_interval_seconds: int = 60,
    ) -> None:
        super().__init__(
            replier=replier,
            user_profile_service=user_profile_service,
            check_interval_seconds=check_interval_seconds,
        )
        self._repository = repository
        self._authorization_service = authorization_service

    async def process_ticks(self, now: Optional[datetime] = None) -> None:
        """執行一次排程檢查。`now` 可注入；不帶時區時視為 UTC。

        所有判定都是「瞬間與瞬間比大小」（門診時間 ± 固定長度、當日結束），
        與伺服器時區無關。需要當地時間的只有文案，由每筆提醒自己的 offset 決定。
        """
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        try:
            missed = await self._repository.mark_missed(current)
        except Exception:
            logger.exception("%s Failed to mark missed appointments", self.LOG_PREFIX)
        else:
            if missed:
                logger.info("%s Marked %d appointment(s) as missed", self.LOG_PREFIX, missed)

        repo = self._repository
        await self._run_stage(
            "T-1h pre reminder",
            repo.list_due_pre_reminders,
            repo.claim_pre_reminder,
            repo.release_pre_reminder,
            self._send_pre_reminder,
            current,
        )
        await self._run_stage(
            "T+0 start reminder",
            repo.list_due_start_reminders,
            repo.claim_start_reminder,
            repo.release_start_reminder,
            self._send_start_reminder,
            current,
        )
        await self._run_stage(
            "T+30 caregiver alert",
            repo.list_due_caregiver_alerts,
            repo.claim_caregiver_alert,
            repo.release_caregiver_alert,
            self._send_caregiver_alert,
            current,
        )

    async def _run_stage(
        self,
        stage: str,
        list_due: Callable[[datetime], Awaitable[List[AppointmentReminder]]],
        claim: Callable[..., Awaitable[bool]],
        release: Callable[[str], Awaitable[bool]],
        send: Callable[[str], Awaitable[bool]],
        now: datetime,
    ) -> None:
        # 每個階段各自吞例外：T-1h 的查詢失敗不該連帶讓 T+30 的家屬警報這一輪不發。
        try:
            due = await list_due(now)
        except Exception:
            logger.exception("%s Failed to list due %s", self.LOG_PREFIX, stage)
            return
        for reminder in due:
            await self._dispatch(
                stage=stage,
                log_id=reminder.id,
                claim=partial(claim, now=now),
                release=release,
                send=partial(send, reminder.id),
            )

    # ── 三個階段 ──────────────────────────────────────────────────────
    #
    # 搶到推播權之後重讀一次文件再組卡片：T+0 要看的是「此刻」有沒有按過出發，
    # 不是清單查出來那一刻。

    async def _send_pre_reminder(self, reminder_id: str) -> bool:
        reminder = await self._repository.get_by_id(reminder_id)
        if reminder is None:
            return True

        def build(patient_name: Optional[str], language: str, font_size: str) -> Any:
            return build_pre_reminder_flex(
                reminder_id=reminder.id,
                when_text=format_when(reminder.local_appointment_at, language),
                hospital_name=reminder.hospital_name,
                patient_name=patient_name,
                language=language,
                font_size=font_size,
            )

        return await self._fan_out(reminder, build, include_patient=True)

    async def _send_start_reminder(self, reminder_id: str) -> bool:
        reminder = await self._repository.get_by_id(reminder_id)
        if reminder is None:
            return True
        departed = reminder.status == "departed"

        def build(patient_name: Optional[str], language: str, font_size: str) -> Any:
            return build_start_reminder_flex(
                reminder_id=reminder.id,
                when_text=format_when(reminder.local_appointment_at, language),
                hospital_name=reminder.hospital_name,
                departed=departed,
                patient_name=patient_name,
                language=language,
                font_size=font_size,
            )

        return await self._fan_out(reminder, build, include_patient=True)

    async def _send_caregiver_alert(self, reminder_id: str) -> bool:
        reminder = await self._repository.get_by_id(reminder_id)
        if reminder is None:
            return True
        departed_time = (
            format_hm(reminder.local(reminder.departed_at))
            if reminder.status == "departed" and reminder.departed_at
            else None
        )

        def build(patient_name: Optional[str], language: str, font_size: str) -> Any:
            return build_caregiver_alert_flex(
                reminder_id=reminder.id,
                when_text=format_when(reminder.local_appointment_at, language),
                hospital_name=reminder.hospital_name,
                patient_name=patient_name or t("flex.appt.fallback_name", language),
                departed_time=departed_time,
                language=language,
                font_size=font_size,
            )

        return await self._fan_out(reminder, build, include_patient=False)

    # ── 收件人 ────────────────────────────────────────────────────────

    async def _fan_out(
        self, reminder: AppointmentReminder, build: _CardBuilder, *, include_patient: bool
    ) -> bool:
        """逐一推給本人（視階段）與家屬，回傳這個階段算不算處理完。

        「至少送達一人」或「沒有任何人該收」都算處理完（回 True）。只有「該收的人
        全部送失敗」才回 False 讓推播權還回去重試——那通常是 LINE 端的整體故障。

        刻意不因為**部分**失敗就重試：重試會把整個階段重送一次，已經收到的人會再收
        一則。某位家屬封鎖了官方帳號（推播永遠失敗）時，那會讓其他每個人都被同一則
        提醒連環轟炸到重試上限。與緊急通報（emergency_alert_service）的判定方式相同。
        """
        attempted = False
        delivered = False

        if include_patient:
            prefs = await self._resolve_prefs(reminder.user_id)
            if prefs.notify_reminder:
                attempted = True
                card = build(None, prefs.language, prefs.font_size)
                delivered = await self._push(reminder.user_id, card) or delivered
            else:
                logger.info(
                    "%s user %s opted out of reminders; skipping patient push for %s",
                    self.LOG_PREFIX,
                    reminder.user_id,
                    reminder.id,
                )

        family = await self._family_recipients(reminder.user_id)
        patient_name = await self._lookup_name(reminder.user_id) if family else None
        for member_id in family:
            prefs = await self._resolve_prefs(member_id)
            if not prefs.notify_family:
                continue
            attempted = True
            card = build(
                patient_name or t("flex.appt.fallback_name", prefs.language),
                prefs.language,
                prefs.font_size,
            )
            delivered = await self._push(member_id, card) or delivered

        return delivered or not attempted

    async def _family_recipients(self, patient_id: str) -> List[str]:
        """家屬名單。本人恆不在其中——他收的是本人版的卡片。

        判定失敗時回空名單（本人照送）：收件人解析是旁路，它壞掉不該讓就診者本人
        也收不到提醒。
        """
        if self._authorization_service is None:
            return []
        try:
            recipients = await self._authorization_service.notification_recipients(
                patient_id, NOTIFICATION_KIND
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "%s 家屬名單判定失敗，本次只送本人：%s", self.LOG_PREFIX, type(exc).__name__
            )
            return []
        unique: List[str] = []
        for uid in recipients or []:
            if uid and uid != patient_id and uid not in unique:
                unique.append(uid)
        return unique

    async def _lookup_name(self, user_id: str) -> Optional[str]:
        """就診者的顯示名稱；查不到回 None，由各收件人依自己的語言填泛稱。"""
        if not self._user_profile_service:
            return None
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:  # noqa: BLE001
            return None
        if isinstance(profile, dict) and profile.get("name"):
            return profile["name"]
        return None

    async def _push(self, user_id: str, card: Any) -> bool:
        try:
            return bool(await self._replier.push_flex(user_id, card))
        except Exception:  # noqa: BLE001
            logger.exception("%s push to %s failed", self.LOG_PREFIX, user_id)
            return False


def start_appointment_scheduler(
    *,
    enabled: bool = True,
    replier: Any,
    repository: Any,
    authorization_service: Any = None,
    user_profile_service: Any = None,
) -> Optional[AppointmentScheduler]:
    if not enabled:
        logger.info("[AppointmentScheduler] disabled")
        return None
    scheduler = AppointmentScheduler(
        replier=replier,
        repository=repository,
        authorization_service=authorization_service,
        user_profile_service=user_profile_service,
    )
    scheduler.start()
    return scheduler
