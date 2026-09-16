"""排程器接上用藥提醒拉霸：送 T+0 時挑語氣與催促時機，T+20 沿用同一種語氣。

拉霸出任何狀況（歷史讀不出來、寫入失敗）都退回現行版本照常送出：提醒不能因為
學習機制出錯而送不出去。長輩關掉提醒的那一頓不進拉霸——訊息沒送出去，沒有東西
可學。T+0 晚送（排程器停過、把時段改到剛過去）的那一頓只進語氣拉霸：催促若照拉霸
挑的 +10 算，可能在送 T+0 的同一輪就緊接著送出。
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.medication import MedicationLog, MedicationReminder
from app.services.line_messaging.flex.medication_flex import MedicationListEntry
from app.services.line_messaging.send_result import SendOutcome, SendResult
from app.services.scheduling.push_tick_scheduler import DispatchOutcome
from app.services.medication.medication_scheduler import (
    MedicationScheduler,
    _TickMedicationNameCache,
)
from app.services.medication.reminder_variants import build_variant_stats

# 最晚服藥時刻（timeout_anchor_time）08:00；T+0 是飯前那批的 07:30。催促要從
# 最晚時刻起算，所以兩者刻意不同。
ANCHOR = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)
SCHEDULED = ANCHOR - timedelta(minutes=30)
TIMEOUT = ANCHOR + timedelta(minutes=30)
ON_TIME = SCHEDULED + timedelta(seconds=30)
LATE = SCHEDULED + timedelta(minutes=12)

CONTROL_T0 = "請於 30 分鐘內服藥，並點擊下方按鈕確認。"
BRIEF_T0 = "吃藥時間到了，吃完按下面的按鈕。"
FAMILY_T0 = "吃完藥按一下下面的按鈕，家人就知道你吃過了。"
BRIEF_T20 = "還沒按喔，吃完藥記得按下面的按鈕。"


def _mean(alpha, beta):
    return alpha / (alpha + beta)


def _pending_log(log_id="L1", **overrides):
    fields = dict(
        id=log_id,
        reminder_id="REM_1",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=SCHEDULED,
        urgent_at=ANCHOR + timedelta(minutes=20),
        timeout_at=TIMEOUT,
        status="pending",
    )
    fields.update(overrides)
    return MedicationLog(**fields)


def _history_favoring(tone, nudge_minutes):
    """其他長輩的紀錄：這個語氣＋時機 30 次都準時按，現行版 30 次都沒按。"""
    wins = [
        _pending_log(
            f"W{i}", user_id="U_OTHER", status="taken", taken_at=ANCHOR,
            reminder_tone=tone, nudge_minutes=nudge_minutes,
        )
        for i in range(30)
    ]
    losses = [
        _pending_log(
            f"X{i}", user_id="U_OTHER", status="missed",
            reminder_tone="control", nudge_minutes=20,
        )
        for i in range(30)
    ]
    return wins + losses


def _stats_favoring(tone, nudge_minutes):
    return build_variant_stats(_history_favoring(tone, nudge_minutes))


def _cache(*logs):
    """預先填好藥名查表，不發查詢（寫法同 test_medication_scheduler.py）。"""
    cache = _TickMedicationNameCache(list(logs))
    cache._entries_by_log_id = {
        log.id: [MedicationListEntry(name="脈優", image_url=None)] for log in logs
    }
    return cache


def _rendered(replier, call=-1):
    return str(replier.push_flex.call_args_list[call][0][1].contents.to_dict())


def _assigned(log_id, *, tone, nudge_minutes, urgent_at, expected_timeout_at):
    """照實模擬「寫入後讀回資料庫那一份」：回傳帶著這次選項的紀錄。"""
    fields = dict(reminder_tone=tone, nudge_minutes=nudge_minutes)
    if urgent_at is not None:
        fields["urgent_at"] = urgent_at
    return _pending_log(log_id, **fields)


def _push_flex_result_via(replier):
    """讓替身同時支援新舊兩種推播介面：排程器現在呼叫 `push_flex_result`，
    既有測試仍以 `push_flex` 設定回傳值與斷言呼叫——`True` 翻成送達、
    `False` 翻成暫時性失敗、例外原樣拋出（由排程器分類）。"""

    async def _push(user_id, card):
        outcome = await replier.push_flex(user_id, card)
        if isinstance(outcome, SendResult):
            return outcome
        return SendResult.success() if outcome else SendResult(SendOutcome.TRANSIENT)

    return AsyncMock(side_effect=_push)


@pytest.fixture()
def replier():
    replier = MagicMock()
    replier.push_flex = AsyncMock(return_value=True)
    replier.push_flex_result = _push_flex_result_via(replier)
    return replier


@pytest.fixture()
def profiles():
    service = MagicMock()
    service.get_user_profile = AsyncMock(return_value={"name": "李老先生"})
    return service


@pytest.fixture()
def log_repository():
    repo = MagicMock()
    repo.list_pending_patient_reminders = AsyncMock(return_value=[])
    repo.list_pending_urgent_reminders = AsyncMock(return_value=[])
    repo.list_pending_caregiver_alerts = AsyncMock(return_value=[])
    repo.claim_patient_reminder = AsyncMock(return_value=True)
    repo.release_patient_reminder = AsyncMock(return_value=True)
    repo.claim_patient_urgent_reminder = AsyncMock(return_value=True)
    repo.release_patient_urgent_reminder = AsyncMock(return_value=True)
    repo.mark_stage_sent = AsyncMock(return_value=True)
    repo.mark_stage_skipped = AsyncMock(return_value=True)
    repo.give_up_stage = AsyncMock(return_value=True)
    repo.cancel_pending_by_reminder = AsyncMock(return_value=1)
    repo.list_variant_outcomes = AsyncMock(return_value=[])
    repo.assign_reminder_variant = AsyncMock(side_effect=_assigned)
    repo.get_log_by_id = AsyncMock(return_value=None)
    return repo


def _scheduler(replier, profiles, log_repository, reminders=None):
    if reminders is None:
        reminders = MagicMock()
        reminders.list_active_reminders_up_to_time = AsyncMock(return_value=[])
        reminders.find_by_ids = AsyncMock(return_value=[])
    medications = MagicMock()
    medications.find_active_by_ids = AsyncMock(return_value=[])
    return MedicationScheduler(
        replier=replier,
        user_profile_service=profiles,
        reminder_repository=reminders,
        log_repository=log_repository,
        medication_repository=medications,
        variant_sample=_mean,
    )


@pytest.fixture()
def scheduler(replier, profiles, log_repository):
    return _scheduler(replier, profiles, log_repository)


# ── T+0：挑選、寫入、照挑到的送 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_on_time_t0_records_the_choice_and_moves_the_nudge_from_the_latest_dose_time(
    scheduler, replier, log_repository
):
    log = _pending_log()

    sent = await scheduler._send_patient_reminder(
        log, _cache(log), _stats_favoring("brief", 10), ON_TIME
    )

    assert sent.ok
    log_repository.assign_reminder_variant.assert_awaited_once_with(
        "L1",
        tone="brief",
        nudge_minutes=10,
        urgent_at=ANCHOR + timedelta(minutes=10),
        expected_timeout_at=TIMEOUT,
    )
    assert BRIEF_T0 in _rendered(replier)
    assert CONTROL_T0 not in _rendered(replier)


@pytest.mark.asyncio
async def test_late_t0_only_enters_the_tone_bandit(scheduler, replier, log_repository):
    """晚了 12 分鐘才送：催促時間維持展開時的 +20，時機不記、不拿來學。"""
    log = _pending_log()

    await scheduler._send_patient_reminder(log, _cache(log), _stats_favoring("brief", 10), LATE)

    log_repository.assign_reminder_variant.assert_awaited_once_with(
        "L1", tone="brief", nudge_minutes=None, urgent_at=None, expected_timeout_at=TIMEOUT
    )
    assert BRIEF_T0 in _rendered(replier)


@pytest.mark.asyncio
async def test_t0_retry_reuses_the_tone_already_stored(scheduler, replier, log_repository):
    """推播失敗後下一輪重送：選項已經寫在紀錄上，不能重挑。"""
    log = _pending_log(reminder_tone="family", nudge_minutes=15)

    await scheduler._send_patient_reminder(log, _cache(log), _stats_favoring("brief", 10), ON_TIME)

    log_repository.assign_reminder_variant.assert_not_awaited()
    assert FAMILY_T0 in _rendered(replier)


@pytest.mark.asyncio
async def test_t0_uses_whatever_the_database_kept(scheduler, replier, log_repository):
    """另一個實例先寫了 family：這次挑到 brief，但卡片照資料庫那一份送。"""
    log_repository.assign_reminder_variant = AsyncMock(
        return_value=_pending_log(reminder_tone="family", nudge_minutes=20)
    )
    log = _pending_log()

    await scheduler._send_patient_reminder(log, _cache(log), _stats_favoring("brief", 10), ON_TIME)

    assert FAMILY_T0 in _rendered(replier)


# ── 拉霸出狀況時照常送 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t0_sends_current_wording_when_history_is_unavailable(
    scheduler, replier, log_repository
):
    log = _pending_log()

    sent = await scheduler._send_patient_reminder(log, _cache(log), None, ON_TIME)

    assert sent.ok
    log_repository.assign_reminder_variant.assert_not_awaited()
    assert CONTROL_T0 in _rendered(replier)


@pytest.mark.asyncio
async def test_t0_follows_the_database_when_the_write_errored_but_landed(
    scheduler, replier, log_repository
):
    """寫入其實成功、只是回應丟了：重讀一次，卡片要跟資料庫記的那一版一致，
    否則 T+0 送現行版、T+20 卻照資料庫送 brief，這一頓的結果也會記錯版本。"""
    log_repository.assign_reminder_variant = AsyncMock(side_effect=RuntimeError("timeout"))
    log_repository.get_log_by_id = AsyncMock(
        return_value=_pending_log(reminder_tone="brief", nudge_minutes=10)
    )
    log = _pending_log()

    sent = await scheduler._send_patient_reminder(
        log, _cache(log), _stats_favoring("brief", 10), ON_TIME
    )

    assert sent.ok
    assert BRIEF_T0 in _rendered(replier)


@pytest.mark.asyncio
async def test_t0_still_sends_when_recording_the_choice_fails(
    scheduler, replier, log_repository
):
    log_repository.assign_reminder_variant = AsyncMock(side_effect=RuntimeError("mongo down"))
    log_repository.get_log_by_id = AsyncMock(side_effect=RuntimeError("mongo down"))
    log = _pending_log()

    sent = await scheduler._send_patient_reminder(
        log, _cache(log), _stats_favoring("brief", 10), ON_TIME
    )

    assert sent.ok
    assert CONTROL_T0 in _rendered(replier)


@pytest.mark.asyncio
async def test_t0_sends_current_wording_when_the_log_cannot_be_read_back(
    scheduler, replier, log_repository
):
    log_repository.assign_reminder_variant = AsyncMock(return_value=None)
    log = _pending_log()

    await scheduler._send_patient_reminder(log, _cache(log), _stats_favoring("brief", 10), ON_TIME)

    assert CONTROL_T0 in _rendered(replier)


@pytest.mark.asyncio
async def test_opted_out_user_is_not_entered_into_the_bandit(
    scheduler, replier, profiles, log_repository
):
    profiles.get_user_profile = AsyncMock(
        return_value={"name": "李老先生", "settings": {"notify_reminder": False}}
    )
    log = _pending_log()

    sent = await scheduler._send_patient_reminder(
        log, _cache(log), _stats_favoring("brief", 10), ON_TIME
    )

    assert sent is DispatchOutcome.SKIPPED
    log_repository.assign_reminder_variant.assert_not_awaited()
    replier.push_flex.assert_not_awaited()


# ── T+20：沿用同一種語氣 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_urgent_reminder_uses_the_tone_chosen_at_t0(scheduler, replier):
    log = _pending_log(reminder_tone="brief", nudge_minutes=10)

    await scheduler._send_urgent_reminder(log, _cache(log))

    assert BRIEF_T20 in _rendered(replier)


# ── 每一輪只讀一次歷史 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_ticks_loads_history_once_for_all_t0_reminders(
    scheduler, replier, log_repository
):
    log_repository.list_pending_patient_reminders.return_value = [
        _pending_log("L1"),
        _pending_log("L2"),
    ]

    await scheduler.process_ticks(now=ON_TIME)

    log_repository.list_variant_outcomes.assert_awaited_once()
    assert replier.push_flex.await_count == 2


@pytest.mark.asyncio
async def test_process_ticks_skips_the_history_query_when_nothing_is_due(
    scheduler, log_repository
):
    await scheduler.process_ticks(now=ON_TIME)

    log_repository.list_variant_outcomes.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_ticks_sends_current_wording_when_the_history_query_fails(
    scheduler, replier, log_repository
):
    log_repository.list_variant_outcomes = AsyncMock(side_effect=RuntimeError("mongo down"))
    log_repository.list_pending_patient_reminders.return_value = [_pending_log()]

    await scheduler.process_ticks(now=ON_TIME)

    log_repository.assign_reminder_variant.assert_not_awaited()
    assert CONTROL_T0 in _rendered(replier)


# ── 三階段實際跑一遍：催促會不會跟 T+0 擠在同一輪 ─────────────────────


class _MemoryLogs:
    """只模擬 process_ticks 用到的那幾個查詢語意（pending、三個旗標、時間門檻），
    讓「催促什麼時候送」由真正的三階段流程決定，而不是由 mock 的回傳值決定。"""

    def __init__(self, history):
        self.docs: dict[str, MedicationLog] = {}
        self.history = history

    async def upsert_log(self, log):
        for doc in self.docs.values():
            if (doc.reminder_id, doc.scheduled_at) == (log.reminder_id, log.scheduled_at):
                return doc, False
        doc = log.model_copy(update={"id": f"L{len(self.docs) + 1}"})
        self.docs[doc.id] = doc
        return doc, True

    async def cancel_pending_by_reminder_ids(self, *args, **kwargs):
        return 0

    async def list_variant_outcomes(self):
        return self.history

    async def get_log_by_id(self, log_id):
        return self.docs.get(log_id)

    async def assign_reminder_variant(
        self, log_id, *, tone, nudge_minutes, urgent_at, expected_timeout_at
    ):
        doc = self.docs[log_id]
        if doc.reminder_tone is None and doc.timeout_at == expected_timeout_at:
            doc.reminder_tone, doc.nudge_minutes = tone, nudge_minutes
            if urgent_at is not None:
                doc.urgent_at = urgent_at
        return doc

    async def list_pending_patient_reminders(self, threshold_time):
        return [
            d for d in self.docs.values()
            if d.status == "pending" and not d.patient_reminder_sent
            and d.scheduled_at <= threshold_time
        ]

    async def list_pending_urgent_reminders(self, threshold_time):
        # 與真正的查詢一致：只催 T+0 真的送達（patient_reminder_sent_at 有值）的。
        return [
            d for d in self.docs.values()
            if d.status == "pending" and d.patient_reminder_sent
            and d.patient_reminder_sent_at is not None
            and not d.urgent_reminder_sent and d.urgent_at <= threshold_time
        ]

    async def list_pending_caregiver_alerts(self, threshold_time):
        return [
            d for d in self.docs.values()
            if d.status == "pending" and not d.caregiver_alert_sent
            and d.patient_reminder_sent_at is not None
            and d.timeout_at <= threshold_time
        ]

    async def mark_stage_sent(self, log_id, stage):
        setattr(self.docs[log_id], f"{stage}_sent_at", datetime.now(timezone.utc))
        return True

    async def mark_stage_skipped(self, log_id, stage):
        setattr(self.docs[log_id], f"{stage}_skipped_at", datetime.now(timezone.utc))
        return True

    async def give_up_stage(self, log_id, stage, error):
        return True

    async def cancel_pending_by_reminder(self, reminder_id):
        return 0

    async def _claim(self, log_id, flag):
        doc = self.docs[log_id]
        if doc.status != "pending" or getattr(doc, flag):
            return False
        setattr(doc, flag, True)
        return True

    async def claim_patient_reminder(self, log_id):
        return await self._claim(log_id, "patient_reminder_sent")

    async def claim_patient_urgent_reminder(self, log_id):
        return await self._claim(log_id, "urgent_reminder_sent")

    async def claim_caregiver_alert(self, log_id):
        return await self._claim(log_id, "caregiver_alert_sent")

    async def _release(self, log_id):
        return False

    release_patient_reminder = release_patient_urgent_reminder = release_caregiver_alert = _release


def _single_dose_reminder():
    """單一條目的 08:00 規則：T+0 與最晚服藥時刻相同，催促 +10 就是 08:10。"""
    reminders = MagicMock()
    reminder = MedicationReminder(
        id="REM_1",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        scheduled_time="08:00",
        start_date="2026-09-14",
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    reminders.list_active_reminders_up_to_time = AsyncMock(return_value=[reminder])
    reminders.find_by_ids = AsyncMock(return_value=[reminder])
    return reminders


def _at(hour, minute, second=0):
    return datetime(2026, 9, 14, hour, minute, second, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_late_t0_is_not_followed_by_a_nudge_in_the_same_tick(replier, profiles):
    """排程器停了 12 分鐘、恢復後才補送 08:00 的 T+0。拉霸若照 +10 算，08:10 早已
    過去，同一輪的催促階段會在幾秒內接著送出「還沒按」。"""
    logs = _MemoryLogs(_history_favoring("brief", 10))
    scheduler = _scheduler(replier, profiles, logs, reminders=_single_dose_reminder())

    await scheduler.process_ticks(now=_at(8, 12))

    assert replier.push_flex.await_count == 1


@pytest.mark.asyncio
async def test_on_time_t0_gets_its_nudge_at_the_chosen_minute(replier, profiles):
    logs = _MemoryLogs(_history_favoring("brief", 10))
    scheduler = _scheduler(replier, profiles, logs, reminders=_single_dose_reminder())

    await scheduler.process_ticks(now=_at(8, 0, 30))
    await scheduler.process_ticks(now=_at(8, 9))
    assert replier.push_flex.await_count == 1

    await scheduler.process_ticks(now=_at(8, 10))
    assert replier.push_flex.await_count == 2
    assert BRIEF_T20 in _rendered(replier)
