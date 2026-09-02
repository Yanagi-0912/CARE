from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.models.medication import TAIPEI_TZ, MedicationLog, MedicationReminder
from app.services.line_messaging.flex.medication_flex import MedicationListEntry
from app.services.medication.medication_scheduler import (
    MedicationScheduler,
    _TickMedicationNameCache,
)


@pytest.fixture()
def mock_replier():
    replier = MagicMock()
    replier.push_flex = AsyncMock(return_value=True)
    return replier


@pytest.fixture()
def mock_user_profile_service():
    service = MagicMock()
    service.get_user_profile = AsyncMock(return_value={"name": "李老先生"})
    return service


@pytest.fixture()
def scheduler(mock_replier, mock_user_profile_service):
    return MedicationScheduler(
        replier=mock_replier,
        user_profile_service=mock_user_profile_service,
        check_interval_seconds=60,
    )


@pytest.mark.asyncio
async def test_process_ticks_t0_initial_reminder(scheduler, mock_replier):
    now = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
    fake_reminder = MedicationReminder(
        id="REM_1",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        scheduled_time="08:00",
        start_date="2026-07-29",
    )
    fake_log = MedicationLog(
        id="LOG_1",
        reminder_id="REM_1",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=now,
        timeout_at=now,
        status="pending",
        patient_reminder_sent=False,
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[fake_reminder],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
        return_value=(fake_log, True),
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_patient_reminders",
        new_callable=AsyncMock,
        return_value=[fake_log],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.claim_patient_reminder",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_claim, patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_urgent_reminders",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_caregiver_alerts",
        new_callable=AsyncMock,
        return_value=[],
    ):
        await scheduler.process_ticks(now=now)

        mock_replier.push_flex.assert_awaited_once()
        call_args = mock_replier.push_flex.call_args[0]
        assert call_args[0] == "U_PATIENT"
        assert call_args[1].type == "flex"
        mock_claim.assert_awaited_once_with("LOG_1")


@pytest.mark.asyncio
async def test_process_ticks_t20_urgent_reminder(scheduler, mock_replier):
    now = datetime(2026, 7, 29, 8, 21, tzinfo=timezone.utc)
    scheduled_at = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
    fake_log = MedicationLog(
        id="LOG_1",
        reminder_id="REM_1",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=scheduled_at,
        timeout_at=scheduled_at,
        status="pending",
        patient_reminder_sent=True,
        urgent_reminder_sent=False,
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_patient_reminders",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_urgent_reminders",
        new_callable=AsyncMock,
        return_value=[fake_log],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_caregiver_alerts",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.claim_patient_urgent_reminder",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_claim:
        await scheduler.process_ticks(now=now)

        mock_replier.push_flex.assert_awaited_once()
        call_args = mock_replier.push_flex.call_args[0]
        assert call_args[0] == "U_PATIENT"
        mock_claim.assert_awaited_once_with("LOG_1")


@pytest.mark.asyncio
async def test_process_ticks_t30_caregiver_alert(scheduler, mock_replier):
    now = datetime(2026, 7, 29, 8, 31, tzinfo=timezone.utc)
    scheduled_at = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
    timeout_at = datetime(2026, 7, 29, 8, 30, tzinfo=timezone.utc)
    fake_log = MedicationLog(
        id="LOG_1",
        reminder_id="REM_1",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=scheduled_at,
        timeout_at=timeout_at,
        status="pending",
        patient_reminder_sent=True,
        urgent_reminder_sent=True,
        caregiver_alert_sent=False,
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_patient_reminders",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_urgent_reminders",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_caregiver_alerts",
        new_callable=AsyncMock,
        return_value=[fake_log],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.claim_caregiver_alert",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_claim:
        await scheduler.process_ticks(now=now)

        mock_replier.push_flex.assert_awaited_once()
        call_args = mock_replier.push_flex.call_args[0]
        assert call_args[0] == "U_CARE"  # Sent to caregiver
        mock_claim.assert_awaited_once_with("LOG_1")


# ── Regression：推播文案的時間必須是台北時間 ──────────────────────────


@pytest.mark.asyncio
async def test_reminder_flex_shows_taipei_time_not_utc(scheduler):
    """
    pymongo 以 naive UTC 讀回 scheduled_at，直接 strftime 會顯示 00:00
    而不是使用者設定的台北 08:00。三個階段的推播文案都必須經過時區轉換。
    """
    now = datetime(2026, 7, 29, 9, 0, tzinfo=TAIPEI_TZ)
    # 台北 08:00 存進 Mongo 再讀回來的樣子
    scheduled_from_db = datetime(2026, 7, 29, 0, 0)
    fake_log = MedicationLog(
        id="LOG_1",
        reminder_id="REM_1",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=scheduled_from_db,
        timeout_at=datetime(2026, 7, 29, 0, 30),
        status="pending",
        patient_reminder_sent=False,
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_patient_reminders",
        new_callable=AsyncMock,
        return_value=[fake_log],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.claim_patient_reminder",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_urgent_reminders",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_caregiver_alerts",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.build_patient_medication_flex"
    ) as mock_build:
        await scheduler.process_ticks(now=now)

        mock_build.assert_called_once()
        assert mock_build.call_args.kwargs["scheduled_time"] == "08:00"


@pytest.mark.asyncio
async def test_caregiver_alert_shows_taipei_time_not_utc(scheduler, mock_replier):
    now = datetime(2026, 7, 29, 9, 0, tzinfo=TAIPEI_TZ)
    fake_log = MedicationLog(
        id="LOG_1",
        reminder_id="REM_1",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=datetime(2026, 7, 29, 0, 0),  # 台北 08:00
        timeout_at=datetime(2026, 7, 29, 0, 30),
        status="pending",
        patient_reminder_sent=True,
        urgent_reminder_sent=True,
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_patient_reminders",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_urgent_reminders",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.list_pending_caregiver_alerts",
        new_callable=AsyncMock,
        return_value=[fake_log],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.claim_caregiver_alert",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.services.medication.medication_scheduler.build_caregiver_alert_flex"
    ) as mock_build:
        await scheduler.process_ticks(now=now)

        mock_build.assert_called_once()
        assert mock_build.call_args.kwargs["scheduled_time"] == "08:00"


# ── Regression：不為「提醒建立之前」的時段補建 log ──────────────────


@contextmanager
def _only_watch_stage_one():
    """把三個推播查詢都關掉，只觀察階段 1 是否補建 log。"""
    prefix = "app.services.medication.medication_scheduler.MedicationLogRepository"
    with patch(
        f"{prefix}.list_pending_patient_reminders", new_callable=AsyncMock, return_value=[]
    ), patch(
        f"{prefix}.list_pending_urgent_reminders", new_callable=AsyncMock, return_value=[]
    ), patch(
        f"{prefix}.list_pending_caregiver_alerts", new_callable=AsyncMock, return_value=[]
    ):
        yield


@pytest.mark.asyncio
async def test_no_backfill_for_slot_before_reminder_was_created(scheduler):
    """
    20:00 新增一筆早上 08:00 的提醒，不該為今天的 08:00 補建 log。
    否則同一個 tick 會連續發出首刷提醒、T+20 催促與 T+30 家屬逾時警報（全是假的）。
    """
    now = datetime(2026, 7, 29, 20, 0, tzinfo=TAIPEI_TZ)
    reminder = MedicationReminder(
        id="REM_1",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        scheduled_time="08:00",
        start_date="2026-07-29",
        # 提醒是今天 20:00 才建立的（資料庫讀回為 naive UTC 12:00）
        created_at=datetime(2026, 7, 29, 12, 0),
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[reminder],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
        side_effect=lambda log: (log, True),
    ) as mock_upsert, _only_watch_stage_one():
        await scheduler.process_ticks(now=now)

        mock_upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_backfill_still_happens_for_reminder_created_earlier(scheduler):
    """停機補建 log 的能力要保留：昨天就建立的提醒，今天仍要補建當日 log。"""
    now = datetime(2026, 7, 29, 20, 0, tzinfo=TAIPEI_TZ)
    reminder = MedicationReminder(
        id="REM_1",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        scheduled_time="08:00",
        start_date="2026-07-28",
        created_at=datetime(2026, 7, 28, 1, 0),  # 昨天建立
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[reminder],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
        side_effect=lambda log: (log, True),
    ) as mock_upsert, _only_watch_stage_one():
        await scheduler.process_ticks(now=now)

        mock_upsert.assert_awaited_once()
        log_arg = mock_upsert.call_args[0][0]
        assert log_arg.scheduled_at == datetime(2026, 7, 29, 8, 0, tzinfo=TAIPEI_TZ)


# ── Regression：停機錯過的時段只留紀錄、不補推播 ─────────────────────


@pytest.mark.asyncio
async def test_misfired_slot_is_recorded_but_silenced(scheduler):
    """
    服務在 08:00 之後才啟動時，早上的時段不該被補推播。

    沒有這道防線的話，20:00 的第一個 tick 會先建立 08:00 的 log，接著同一個 tick
    內三個階段依序判定成立：首刷、T+20 催促、T+30 家屬逾時警報一次全發。
    """
    now = datetime(2026, 7, 29, 20, 0, tzinfo=TAIPEI_TZ)
    reminder = MedicationReminder(
        id="REM_1",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        scheduled_time="08:00",
        start_date="2026-07-28",
        created_at=datetime(2026, 7, 28, 1, 0),
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[reminder],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
        side_effect=lambda log: (log, True),
    ) as mock_upsert, _only_watch_stage_one():
        await scheduler.process_ticks(now=now)

        log_arg = mock_upsert.call_args[0][0]
        # 紀錄仍要留下，讓使用者與家屬事後看得到這一餐沒吃
        assert log_arg.status == "missed"
        # 三個旗標全設起，後續 tick 的三個查詢都不會再撈到它
        assert log_arg.patient_reminder_sent is True
        assert log_arg.urgent_reminder_sent is True
        assert log_arg.caregiver_alert_sent is True


@pytest.mark.asyncio
async def test_slot_within_grace_window_still_pushes(scheduler):
    """短暫部署造成的延遲仍要正常送達：08:00 的時段在 08:15 才建 log，照常推播。"""
    now = datetime(2026, 7, 29, 8, 15, tzinfo=TAIPEI_TZ)
    reminder = MedicationReminder(
        id="REM_1",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        scheduled_time="08:00",
        start_date="2026-07-28",
        created_at=datetime(2026, 7, 28, 1, 0),
    )

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[reminder],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
        side_effect=lambda log: (log, True),
    ) as mock_upsert, _only_watch_stage_one():
        await scheduler.process_ticks(now=now)

        log_arg = mock_upsert.call_args[0][0]
        assert log_arg.status == "pending"
        assert log_arg.patient_reminder_sent is False


# ── 停機錯過的時段：依家屬彙整成一則通知 ─────────────────────────────


def _misfired_reminders() -> list[MedicationReminder]:
    """兩個都早於 grace window 的時段，通報對象同為 U_CARE。"""
    return [
        MedicationReminder(
            id=f"REM_{slot}",
            creator_user_id="U_CARE",
            user_id="U_PATIENT",
            slot_type=slot,
            scheduled_time=time_str,
            start_date="2026-07-28",
            created_at=datetime(2026, 7, 28, 1, 0),
        )
        for slot, time_str in (("morning", "08:00"), ("noon", "12:00"))
    ]


@pytest.mark.asyncio
async def test_misfired_slots_are_summarised_into_one_message(scheduler, mock_replier):
    """
    停機期間錯過的多個時段，對同一位家屬只發一則彙整通知。

    逐則發送的話，停機半天 × 多位家人會在同一個 tick 內變成數十則轟炸。
    """
    now = datetime(2026, 7, 29, 20, 0, tzinfo=TAIPEI_TZ)

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=_misfired_reminders(),
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
        side_effect=lambda log: (log, True),
    ), patch(
        "app.services.medication.medication_scheduler.build_caregiver_missed_summary_flex"
    ) as mock_build, _only_watch_stage_one():
        await scheduler.process_ticks(now=now)

        mock_replier.push_flex.assert_awaited_once()
        assert mock_replier.push_flex.call_args[0][0] == "U_CARE"

        entries = mock_build.call_args.kwargs["missed"]
        # 兩個時段收在同一則裡，且依時間排序
        assert [e["scheduled_time"] for e in entries] == ["08:00", "12:00"]
        assert [e["slot_type"] for e in entries] == ["morning", "noon"]
        assert {e["patient_name"] for e in entries} == {"李老先生"}


@pytest.mark.asyncio
async def test_misfired_summary_not_resent_on_later_ticks(scheduler, mock_replier):
    """
    created=False 代表這筆 log 先前的 tick 就建好了，不能再通知一次。

    is_misfired 每個 tick 都會重新算出同一批結果，只靠它判斷的話家屬會每 60 秒
    被同一則通知洗版。
    """
    now = datetime(2026, 7, 29, 20, 0, tzinfo=TAIPEI_TZ)

    with patch(
        "app.services.medication.medication_scheduler.MedicationReminderRepository.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=_misfired_reminders(),
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
        side_effect=lambda log: (log, False),  # 早就建好了
    ), _only_watch_stage_one():
        await scheduler.process_ticks(now=now)

        mock_replier.push_flex.assert_not_awaited()


# ── Regression：推播權搶佔（多實例並存時不重複推播）─────────────────


@contextmanager
def _only_watch_stage_one_b(pending_logs):
    """只讓階段 1b（首刷推播）有資料，其餘查詢一律回空。"""
    reminder_prefix = "app.services.medication.medication_scheduler.MedicationReminderRepository"
    log_prefix = "app.services.medication.medication_scheduler.MedicationLogRepository"
    with patch(
        f"{reminder_prefix}.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        f"{log_prefix}.list_pending_patient_reminders",
        new_callable=AsyncMock,
        return_value=pending_logs,
    ), patch(
        f"{log_prefix}.list_pending_urgent_reminders", new_callable=AsyncMock, return_value=[]
    ), patch(
        f"{log_prefix}.list_pending_caregiver_alerts", new_callable=AsyncMock, return_value=[]
    ):
        yield


def _pending_log() -> MedicationLog:
    scheduled_at = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
    return MedicationLog(
        id="LOG_1",
        reminder_id="REM_1",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=scheduled_at,
        timeout_at=scheduled_at,
        status="pending",
        patient_reminder_sent=False,
    )


@pytest.mark.asyncio
async def test_lost_claim_skips_push(scheduler, mock_replier):
    """
    搶不到推播權就不推播。

    滾動更新期間新舊 pod 必然重疊（maxUnavailable=0 + maxSurge=1），兩邊會查到同一筆
    未送出的 log；先搶到旗標的那個實例才負責送，否則使用者收到兩則相同提醒。
    """
    now = datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc)

    with _only_watch_stage_one_b([_pending_log()]), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.claim_patient_reminder",
        new_callable=AsyncMock,
        return_value=False,  # 另一個實例先搶走了
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.release_patient_reminder",
        new_callable=AsyncMock,
    ) as mock_release:
        await scheduler.process_ticks(now=now)

        mock_replier.push_flex.assert_not_awaited()
        # 沒搶到就沒有推播權可還，不能去動別的實例已設起的旗標
        mock_release.assert_not_awaited()


@pytest.mark.asyncio
async def test_push_failure_releases_claim(scheduler, mock_replier):
    """推播失敗要把旗標還原，否則這則提醒永遠不會再被重試。"""
    now = datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc)
    mock_replier.push_flex = AsyncMock(return_value=False)

    with _only_watch_stage_one_b([_pending_log()]), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.claim_patient_reminder",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.release_patient_reminder",
        new_callable=AsyncMock,
    ) as mock_release:
        await scheduler.process_ticks(now=now)

        mock_replier.push_flex.assert_awaited_once()
        mock_release.assert_awaited_once_with("LOG_1")


@pytest.mark.asyncio
async def test_push_exception_releases_claim(scheduler, mock_replier):
    """推播丟例外同樣要還原旗標，並且不能讓整個 tick 中斷。"""
    now = datetime(2026, 7, 29, 8, 1, tzinfo=timezone.utc)
    mock_replier.push_flex = AsyncMock(side_effect=RuntimeError("LINE API down"))

    with _only_watch_stage_one_b([_pending_log()]), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.claim_patient_reminder",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.release_patient_reminder",
        new_callable=AsyncMock,
    ) as mock_release:
        await scheduler.process_ticks(now=now)

        mock_release.assert_awaited_once_with("LOG_1")


# ── 推播文案的藥品區塊：只在組裝文案時解析，展開路徑不讀 medication_ids ──
#
# 這裡刻意不透過 process_ticks 走完整流程來驗證，而是直接測試
# `_TickMedicationNameCache`：它是新增邏輯唯一讀 medication_ids、也是唯一發出
# 「查規則」「查藥品」這兩種查詢的地方，且用 collection= 注入假的 collection，
# 不需要 monkeypatch 掉 MedicationReminderRepository／MedicationRepository
# 這兩個 static class。


def _find_collection(docs=None, *, raise_error: Exception | None = None):
    """
    建立一個假的 Motor collection，模擬 `find(...).to_list(...)` 的行為——
    這是 `MedicationReminderRepository.find_by_ids` 與
    `MedicationRepository.find_active_by_ids` 共用的查詢形狀。
    """
    col = MagicMock()
    cursor = MagicMock()
    if raise_error is not None:
        cursor.to_list = AsyncMock(side_effect=raise_error)
    else:
        cursor.to_list = AsyncMock(return_value=docs or [])
    col.find = MagicMock(return_value=cursor)
    return col


def _log(log_id: str, reminder_id: str, scheduled_at: datetime) -> MedicationLog:
    return MedicationLog(
        id=log_id,
        reminder_id=reminder_id,
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=scheduled_at,
        timeout_at=scheduled_at,
        status="pending",
    )


@pytest.mark.asyncio
async def test_tick_cache_batches_queries_at_constant_count_regardless_of_log_count():
    """
    Finding 1 的核心主張：N 筆 log 共用同一份查表時，查詢次數是常數，不隨 N 增加。
    這裡用 6 筆 log（對應 3 個不同的 reminder，模擬多位使用者共用同一個 08:00
    時段）驗證：無論 `.get()` 被呼叫幾次，`find_by_ids` 與 `find_active_by_ids`
    各自只真正發出一次查詢。
    """
    scheduled_at = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)  # 台北 08:00
    logs = [
        _log("L1", "REM_1", scheduled_at),
        _log("L2", "REM_1", scheduled_at),  # 同一個 reminder 的其他 log 也算進來
        _log("L3", "REM_2", scheduled_at),
        _log("L4", "REM_2", scheduled_at),
        _log("L5", "REM_3", scheduled_at),
        _log("L6", "REM_3", scheduled_at),
    ]
    reminder_docs = [
        {"_id": "REM_1", "creator_user_id": "U_CARE", "user_id": "U_P1", "slot_type": "morning", "scheduled_time": "08:00", "medication_ids": ["M1"]},
        {"_id": "REM_2", "creator_user_id": "U_CARE", "user_id": "U_P2", "slot_type": "morning", "scheduled_time": "08:00", "medication_ids": ["M2"]},
        {"_id": "REM_3", "creator_user_id": "U_CARE", "user_id": "U_P3", "slot_type": "morning", "scheduled_time": "08:00", "medication_ids": []},
    ]
    medication_docs = [
        {"_id": "M1", "user_id": "U_P1", "created_by_user_id": "U_CARE", "name": "脈優"},
        {"_id": "M2", "user_id": "U_P2", "created_by_user_id": "U_CARE", "name": "利尿劑"},
    ]
    reminder_col = _find_collection(reminder_docs)
    medication_col = _find_collection(medication_docs)

    cache = _TickMedicationNameCache(logs)
    results = {
        log.id: await cache.get(
            log, reminder_collection=reminder_col, medication_collection=medication_col
        )
        for log in logs
    }

    assert results == {
        "L1": ["脈優"],
        "L2": ["脈優"],
        "L3": ["利尿劑"],
        "L4": ["利尿劑"],
        "L5": [],
        "L6": [],
    }
    # 重點斷言：查詢次數是常數（各 1 次），不是每筆 log 都各查一次（會是 6 次）。
    reminder_col.find.assert_called_once()
    medication_col.find.assert_called_once()
    (reminder_query,), _ = reminder_col.find.call_args
    assert reminder_query == {"_id": {"$in": ["REM_1", "REM_2", "REM_3"]}}
    (medication_query,), _ = medication_col.find.call_args
    assert medication_query["_id"] == {"$in": ["M1", "M2"]}


@pytest.mark.asyncio
async def test_tick_cache_skips_medication_query_when_no_reminder_has_medication_ids():
    """既有規則（medication_ids 皆為空）常態：查完規則後發現沒有任何藥品 id，
    直接省下第二趟查詢——不是查回空清單，而是根本不發這個查詢。"""
    scheduled_at = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
    logs = [_log("L1", "REM_1", scheduled_at)]
    reminder_docs = [
        {
            "_id": "REM_1",
            "creator_user_id": "U_CARE",
            "user_id": "U_PATIENT",
            "slot_type": "morning",
            "scheduled_time": "08:00",
            # 沒有 medication_ids 鍵，模擬本變更前寫入的規則
        }
    ]
    reminder_col = _find_collection(reminder_docs)
    medication_col = _find_collection([])

    cache = _TickMedicationNameCache(logs)
    names = await cache.get(
        logs[0], reminder_collection=reminder_col, medication_collection=medication_col
    )

    assert names == []
    medication_col.find.assert_not_called()


@pytest.mark.asyncio
async def test_tick_cache_excludes_drug_filtered_out_by_repository():
    """
    藥品失效（停用或療程已結束）時，find_active_by_ids 就不會把它撈出來；
    這裡驗證整批查表老實把查詢結果轉成名稱清單，失效藥品不會出現。
    """
    scheduled_at = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
    logs = [_log("L1", "REM_1", scheduled_at)]
    reminder_docs = [
        {
            "_id": "REM_1",
            "creator_user_id": "U_CARE",
            "user_id": "U_PATIENT",
            "slot_type": "morning",
            "scheduled_time": "08:00",
            "medication_ids": ["M1", "M2"],
        }
    ]
    # M2 已停用／過期，模擬 find_active_by_ids 在 DB 端就把它濾掉
    medication_docs = [
        {"_id": "M1", "user_id": "U_PATIENT", "created_by_user_id": "U_CARE", "name": "脈優"},
    ]
    reminder_col = _find_collection(reminder_docs)
    medication_col = _find_collection(medication_docs)

    cache = _TickMedicationNameCache(logs)
    names = await cache.get(
        logs[0], reminder_collection=reminder_col, medication_collection=medication_col
    )

    assert names == ["脈優"]


@pytest.mark.asyncio
async def test_tick_cache_missing_reminder_yields_empty_for_that_log():
    """規則批次查詢查不到某個 reminder_id（例如剛好被刪除）時，該筆 log 的
    藥品清單退化為空，不影響其他 log。"""
    scheduled_at = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
    logs = [_log("L1", "REM_MISSING", scheduled_at), _log("L2", "REM_1", scheduled_at)]
    reminder_docs = [
        {
            "_id": "REM_1",
            "creator_user_id": "U_CARE",
            "user_id": "U_PATIENT",
            "slot_type": "morning",
            "scheduled_time": "08:00",
            "medication_ids": ["M1"],
        }
    ]
    medication_docs = [
        {"_id": "M1", "user_id": "U_PATIENT", "created_by_user_id": "U_CARE", "name": "脈優"},
    ]
    reminder_col = _find_collection(reminder_docs)
    medication_col = _find_collection(medication_docs)

    cache = _TickMedicationNameCache(logs)
    names_missing = await cache.get(
        logs[0], reminder_collection=reminder_col, medication_collection=medication_col
    )
    names_found = await cache.get(
        logs[1], reminder_collection=reminder_col, medication_collection=medication_col
    )

    assert names_missing == []
    assert names_found == ["脈優"]


@pytest.mark.asyncio
async def test_tick_cache_reminder_batch_failure_degrades_to_empty_for_every_log():
    """
    整批「查規則」的查詢拋例外時，這一批所有 log 的推播都要照常送出，只是藥品
    清單全部退化為空——不是只有第一筆失敗，而是整批共用的失敗結果。同時要確認
    這個失敗只讓查詢真的發生一次，不會因為後續 log 呼叫 `.get()` 就重試。
    """
    scheduled_at = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
    logs = [
        _log("L1", "REM_1", scheduled_at),
        _log("L2", "REM_2", scheduled_at),
        _log("L3", "REM_3", scheduled_at),
    ]
    reminder_col = _find_collection(raise_error=RuntimeError("Mongo down"))
    medication_col = _find_collection([])

    cache = _TickMedicationNameCache(logs)
    results = [
        await cache.get(
            log, reminder_collection=reminder_col, medication_collection=medication_col
        )
        for log in logs
    ]

    assert results == [[], [], []]
    reminder_col.find.assert_called_once()
    # 規則都查不到，藥品查詢完全不會被觸發。
    medication_col.find.assert_not_called()


@pytest.mark.asyncio
async def test_tick_cache_medication_batch_failure_degrades_to_empty_for_every_log():
    """
    規則查詢成功、但整批「查藥品」的查詢拋例外時，同樣要讓這一批所有 log 的
    藥品清單退化為空，且查詢只嘗試一次。
    """
    scheduled_at = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
    logs = [
        _log("L1", "REM_1", scheduled_at),
        _log("L2", "REM_2", scheduled_at),
    ]
    reminder_docs = [
        {"_id": "REM_1", "creator_user_id": "U_CARE", "user_id": "U_P1", "slot_type": "morning", "scheduled_time": "08:00", "medication_ids": ["M1"]},
        {"_id": "REM_2", "creator_user_id": "U_CARE", "user_id": "U_P2", "slot_type": "morning", "scheduled_time": "08:00", "medication_ids": ["M2"]},
    ]
    reminder_col = _find_collection(reminder_docs)
    medication_col = _find_collection(raise_error=RuntimeError("Mongo down"))

    cache = _TickMedicationNameCache(logs)
    results = [
        await cache.get(
            log, reminder_collection=reminder_col, medication_collection=medication_col
        )
        for log in logs
    ]

    assert results == [[], []]
    medication_col.find.assert_called_once()


# ── 縮圖 URL 的解析：沿用同一批查詢，不得新增每筆 log 的額外查詢 ──────────
#
# 縮圖解析走 `_TickMedicationNameCache.__init__` 的 `resolve_image_url` 參數
# 注入（預設是真正的 `resolve_drug_appearance_image_url`），不是全域函式呼叫，
# 所以這裡可以直接塞一個假解析器，不需要 monkeypatch 掉
# `drug_appearance_image_service` 模組。


def _thumbnail_resolver(url_by_license: dict[str, str]):
    """假縮圖解析器：介面比照 `resolve_drug_appearance_image_url`
    （license_number -> URL 或 None），不觸碰檔案系統或 settings。`.calls` 記錄
    每次呼叫的證號，用來驗證「解析次數是常數、不隨 log 數量增加」。
    """
    calls: list[str] = []

    def _resolve(license_number: str) -> Optional[str]:
        calls.append(license_number)
        return url_by_license.get(license_number)

    _resolve.calls = calls  # type: ignore[attr-defined]
    return _resolve


@pytest.mark.asyncio
async def test_tick_cache_thumbnail_resolution_adds_no_per_log_queries():
    """
    縮圖解析必須沿用「查規則」「查藥品」同一批結果，不能讓 log 數量拉高查詢
    次數——形狀比照 test_tick_cache_batches_queries_at_constant_count_regardless_of_log_count，
    差別只在這裡額外驗證縮圖解析函式本身也只對「不重複的藥品」各呼叫一次
    （2 種藥），不是每筆 log 各呼叫一次（6 次）。
    """
    scheduled_at = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
    logs = [
        _log("L1", "REM_1", scheduled_at),
        _log("L2", "REM_1", scheduled_at),
        _log("L3", "REM_2", scheduled_at),
        _log("L4", "REM_2", scheduled_at),
        _log("L5", "REM_3", scheduled_at),
        _log("L6", "REM_3", scheduled_at),
    ]
    reminder_docs = [
        {"_id": "REM_1", "creator_user_id": "U_CARE", "user_id": "U_P1", "slot_type": "morning", "scheduled_time": "08:00", "medication_ids": ["M1"]},
        {"_id": "REM_2", "creator_user_id": "U_CARE", "user_id": "U_P2", "slot_type": "morning", "scheduled_time": "08:00", "medication_ids": ["M2"]},
        {"_id": "REM_3", "creator_user_id": "U_CARE", "user_id": "U_P3", "slot_type": "morning", "scheduled_time": "08:00", "medication_ids": []},
    ]
    medication_docs = [
        {"_id": "M1", "user_id": "U_P1", "created_by_user_id": "U_CARE", "name": "脈優", "license_number": "LIC-001"},
        {"_id": "M2", "user_id": "U_P2", "created_by_user_id": "U_CARE", "name": "利尿劑", "license_number": "LIC-002"},
    ]
    reminder_col = _find_collection(reminder_docs)
    medication_col = _find_collection(medication_docs)
    resolver = _thumbnail_resolver(
        {"LIC-001": "https://img.example.com/a.jpg", "LIC-002": "https://img.example.com/b.jpg"}
    )

    cache = _TickMedicationNameCache(logs, resolve_image_url=resolver)
    results = {
        log.id: await cache.get_entries(
            log, reminder_collection=reminder_col, medication_collection=medication_col
        )
        for log in logs
    }

    assert results["L1"] == [
        MedicationListEntry(name="脈優", image_url="https://img.example.com/a.jpg")
    ]
    assert results["L2"] == results["L1"]
    assert results["L3"] == [
        MedicationListEntry(name="利尿劑", image_url="https://img.example.com/b.jpg")
    ]
    assert results["L4"] == results["L3"]
    assert results["L5"] == []
    assert results["L6"] == []

    # 重點斷言：DB 查詢次數是常數，不隨 log 數量增加。
    reminder_col.find.assert_called_once()
    medication_col.find.assert_called_once()
    # 縮圖解析同樣是常數次（只對 2 種不重複的藥各解析一次），不是 6 次。
    assert resolver.calls == ["LIC-001", "LIC-002"]


@pytest.mark.asyncio
async def test_tick_cache_drug_with_empty_license_number_gets_no_thumbnail():
    """
    spec「證號不確定時不得顯示藥丸照片」：license_number 為空字串時一律不得帶出
    縮圖，且解析函式根本不會被呼叫——不是「呼叫後被判定不能用」，是這裡的把關
    直接擋下，不讓一個「證號未確定」的藥品有機會被解析出任何 URL。
    """
    scheduled_at = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
    logs = [_log("L1", "REM_1", scheduled_at)]
    reminder_docs = [
        {"_id": "REM_1", "creator_user_id": "U_CARE", "user_id": "U_PATIENT", "slot_type": "morning", "scheduled_time": "08:00", "medication_ids": ["M1"]},
    ]
    medication_docs = [
        {"_id": "M1", "user_id": "U_PATIENT", "created_by_user_id": "U_CARE", "name": "脈優", "license_number": ""},
    ]
    reminder_col = _find_collection(reminder_docs)
    medication_col = _find_collection(medication_docs)
    resolver = _thumbnail_resolver({"": "https://img.example.com/should-not-be-used.jpg"})

    cache = _TickMedicationNameCache(logs, resolve_image_url=resolver)
    entries = await cache.get_entries(
        logs[0], reminder_collection=reminder_col, medication_collection=medication_col
    )

    assert entries == [MedicationListEntry(name="脈優", image_url=None)]
    assert resolver.calls == []


@pytest.mark.asyncio
async def test_tick_cache_thumbnail_resolution_failure_degrades_without_raising():
    """
    縮圖解析失敗（例如底層檔案系統或未來換成的服務丟例外）不能讓整批藥品清單
    查詢連坐失敗：該筆藥品退化為沒有縮圖，藥名與清單其餘部分照常返回。
    """
    scheduled_at = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
    logs = [_log("L1", "REM_1", scheduled_at)]
    reminder_docs = [
        {"_id": "REM_1", "creator_user_id": "U_CARE", "user_id": "U_PATIENT", "slot_type": "morning", "scheduled_time": "08:00", "medication_ids": ["M1"]},
    ]
    medication_docs = [
        {"_id": "M1", "user_id": "U_PATIENT", "created_by_user_id": "U_CARE", "name": "脈優", "license_number": "LIC-001"},
    ]
    reminder_col = _find_collection(reminder_docs)
    medication_col = _find_collection(medication_docs)

    def _raising_resolver(license_number: str) -> Optional[str]:
        raise RuntimeError("thumbnail service down")

    cache = _TickMedicationNameCache(logs, resolve_image_url=_raising_resolver)
    entries = await cache.get_entries(
        logs[0], reminder_collection=reminder_col, medication_collection=medication_col
    )

    assert entries == [MedicationListEntry(name="脈優", image_url=None)]


@pytest.mark.asyncio
async def test_send_patient_reminder_pushes_even_when_entries_have_no_thumbnail(
    scheduler, mock_replier
):
    """
    這裡驗證的是「送出這一端」不依賴縮圖是否存在——即使拿到的是已經退化為沒有
    image_url 的 entries，push_flex 仍然照常被呼叫、回傳成功，不會因為缺了圖片
    就不送。

    這不是「縮圖解析失敗會被擋下」本身的證明：本測試直接預先塞好已退化的
    entries，繞過 `_load()`，所以解析函式根本沒被呼叫到。「解析失敗不會拋出、
    會被 `_load()` 擋下」這件事由
    `test_tick_cache_thumbnail_resolution_failure_degrades_without_raising`
    單獨驗證；兩則測試合起來才完整覆蓋「解析失敗 → 不拋出 → 送出端不受影響」
    這條鏈。
    """
    scheduled_at = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
    log = _log("L1", "REM_1", scheduled_at)
    cache = _TickMedicationNameCache([log])
    # 模擬 _load() 已經因為縮圖解析失敗而把這筆藥品退化為沒有 image_url。
    cache._entries_by_log_id = {"L1": [MedicationListEntry(name="脈優", image_url=None)]}

    sent = await scheduler._send_patient_reminder(log, cache)

    assert sent is True
    mock_replier.push_flex.assert_awaited_once()
    call_args = mock_replier.push_flex.call_args[0]
    rendered = str(call_args[1].contents.to_dict())
    assert "脈優" in rendered
    # dict 轉字串後圖片節點會是 "'type': 'image'"（Python repr 用單引號）
    assert "'type': 'image'" not in rendered


@pytest.mark.asyncio
async def test_send_patient_reminder_reads_names_from_shared_cache(scheduler, mock_replier):
    """
    整合點檢查：`_send_patient_reminder` 真的把 cache 解析出的「藥名＋縮圖」
    餵進 flex builder，而不是自己另外查一次、也不是只挑了名字把縮圖丟掉。
    這裡直接呼叫該方法（不透過 process_ticks），確認 claim 之外的組裝流程
    正確串起來。

    entries 特意帶入真正的 image_url（不是 None）：這是唯一能鎖住
    `_send_patient_reminder` 呼叫的是 `get_entries()` 而非 `get()` 的地方——
    若日後被改回呼叫 `get()`（等同縮圖功能導入前的呼叫方式），image_url 會在
    `.get()` 內被丟棄，這裡斷言的 URL 就不會出現在渲染結果，測試會失敗。

    直接預先填好 cache 內部的查表結果，不必真的發查詢——這個測試要驗證的是
    「組裝文案時讀 cache」這件事本身，查表怎麼被填滿已經由上面幾個
    `_TickMedicationNameCache` 的測試涵蓋了。
    """
    scheduled_at = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
    log = _log("L1", "REM_1", scheduled_at)
    cache = _TickMedicationNameCache([log])
    cache._entries_by_log_id = {
        "L1": [MedicationListEntry(name="脈優", image_url="https://img.example.com/a.jpg")]
    }

    sent = await scheduler._send_patient_reminder(log, cache)

    assert sent is True
    mock_replier.push_flex.assert_awaited_once()
    call_args = mock_replier.push_flex.call_args[0]
    rendered = str(call_args[1].contents.to_dict())
    assert "脈優" in rendered
    assert "https://img.example.com/a.jpg" in rendered


@pytest.mark.asyncio
async def test_send_urgent_reminder_reads_names_from_shared_cache(scheduler, mock_replier):
    """同上：帶真正的 image_url，鎖住 `_send_urgent_reminder` 呼叫的是
    `get_entries()` 而不是把縮圖丟掉的 `get()`。"""
    scheduled_at = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
    log = _log("L1", "REM_1", scheduled_at)
    cache = _TickMedicationNameCache([log])
    cache._entries_by_log_id = {
        "L1": [MedicationListEntry(name="普拿疼", image_url="https://img.example.com/b.jpg")]
    }

    sent = await scheduler._send_urgent_reminder(log, cache)

    assert sent is True
    mock_replier.push_flex.assert_awaited_once()
    call_args = mock_replier.push_flex.call_args[0]
    rendered = str(call_args[1].contents.to_dict())
    assert "普拿疼" in rendered
    assert "https://img.example.com/b.jpg" in rendered


@pytest.mark.asyncio
async def test_send_caregiver_alert_reads_names_from_shared_cache(
    scheduler, mock_replier
):
    """
    T+30 的家屬警報要說得出漏掉的是哪幾種藥，且藥名同樣從該階段共用的查表取得
    ——不是自己另外查一次。它與 T+0／T+20 的差別只在收件人是家屬，沒有理由
    在藥名解析上另起爐灶。

    entries 特意帶入真正的 image_url（不是 None）：這是唯一能鎖住
    `_send_caregiver_alert` 呼叫的是「只回藥名」的 `get()` 而非
    `get_entries()` 的地方。改用純字串清單當 fixture 只是「剛好」讓縮圖不可能
    出現——那是巧合，不是保證；若日後 `_send_caregiver_alert` 被改成呼叫
    `get_entries()`（例如複製貼上 `_send_patient_reminder` 時忘了改），這裡
    帶真正 URL 的 entries 就會讓縮圖真的出現在渲染結果，下面的斷言才攔得住
    （spec「家屬卡片不含縮圖」）。
    """
    scheduled_at = datetime(2026, 8, 9, 0, 0, tzinfo=timezone.utc)
    log = _log("L1", "REM_1", scheduled_at)
    cache = _TickMedicationNameCache([log])
    cache._entries_by_log_id = {
        "L1": [
            MedicationListEntry(name="脈優", image_url="https://img.example.com/a.jpg"),
            MedicationListEntry(name="利尿劑", image_url="https://img.example.com/b.jpg"),
        ]
    }

    sent = await scheduler._send_caregiver_alert(log, cache)

    assert sent is True
    mock_replier.push_flex.assert_awaited_once()
    call_args = mock_replier.push_flex.call_args[0]
    assert call_args[0] == "U_CARE"  # 收件人仍是通報家屬
    rendered = str(call_args[1].contents.to_dict())
    assert "脈優" in rendered
    assert "利尿劑" in rendered
    assert "尚未服用的藥品" in rendered
    # 核心斷言（spec「家屬卡片不含縮圖」）：即使 cache 裡的 entries 帶著真正的
    # image_url，家屬警報渲染出來也不能有任何圖片節點，URL 本身也不能外流。
    assert "'type': 'image'" not in rendered
    assert "https://img.example.com" not in rendered


def test_process_ticks_expansion_path_does_not_reference_medication_ids():
    """
    展開與搶佔路徑不得讀 medication_ids —— 這是排程器既有併發保證（原子搶佔、
    唯一索引、停機補償）能夠成立的前提，見 openspec 決策。藥名解析已刻意搬到
    `_TickMedicationNameCache`，只在組裝推播文案時才呼叫；這裡用原始碼掃描
    鎖住 process_ticks 本體不會再讀這個欄位。
    """
    import inspect

    source = inspect.getsource(MedicationScheduler.process_ticks)
    assert "medication_ids" not in source


def test_process_ticks_builds_exactly_one_cache_per_stage_outside_the_loop():
    """
    Finding 1 的批次化必須是「每個階段建立一次查表物件」，而不是在迴圈裡逐筆
    建立（逐筆建立會讓批次化形同虛設，因為每個物件只服務一筆 log）。這裡用
    原始碼掃描鎖住：`_TickMedicationNameCache(` 只會出現三次（T+0、T+20、T+30
    各一次），且都在各自的 `for log in ...` 迴圈之前。

    （T+30 是後來才加入的：家屬警報開始列出漏服的藥品後，它與前兩個階段一樣
    需要藥名，同一個 tick 內同樣可能有多筆 log，沒有理由退回逐筆查詢。）
    """
    import inspect

    source = inspect.getsource(MedicationScheduler.process_ticks)
    assert source.count("_TickMedicationNameCache(") == 3

    initial_cache_pos = source.index("_TickMedicationNameCache(pending_initial_logs)")
    initial_loop_pos = source.index("for log in pending_initial_logs:")
    assert initial_cache_pos < initial_loop_pos

    urgent_cache_pos = source.index("_TickMedicationNameCache(pending_urgent_logs)")
    urgent_loop_pos = source.index("for log in pending_urgent_logs:")
    assert urgent_cache_pos < urgent_loop_pos

    alert_cache_pos = source.index("_TickMedicationNameCache(pending_alert_logs)")
    alert_loop_pos = source.index("for log in pending_alert_logs:")
    assert alert_cache_pos < alert_loop_pos



# ── 療程結束後仍推播空卡片的回歸防護 ────────────────────────────────
#
# 規則的 end_date 由 find_or_create_reminder 一律寫成 None（長期有效），藥品的
# end_date 則由處方箋療程天數換算。療程結束後兩邊脫鉤：規則照常展開，藥品清單
# 卻已全數失效，推出去的是一張說不出要吃什麼的空卡片。以下四個測試把「要不要
# 推」與「今天還有沒有有效的藥」綁在一起，並保住「沒掛藥的舊規則照常推播」。


def _reminder_with_medications(medication_ids, reminder_id="REM_1"):
    return MedicationReminder(
        id=reminder_id,
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        scheduled_time="08:00",
        start_date="2026-08-17",
        end_date=None,
        medication_ids=medication_ids,
        created_at=datetime(2026, 8, 17, 0, 0),
    )


def _active_medication(medication_id):
    from app.models.medication import Medication

    return Medication(
        id=medication_id,
        user_id="U_PATIENT",
        created_by_user_id="U_CARE",
        name="西美胃錠200MG",
        start_date="2026-08-17",
        end_date="2026-12-31",
    )


@contextmanager
def _stage_one_with_active_medications(reminders, active_medications):
    """只觀察階段 1，並代入 find_active_by_ids 的回傳值。"""
    reminder_prefix = (
        "app.services.medication.medication_scheduler.MedicationReminderRepository"
    )
    with patch(
        f"{reminder_prefix}.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=reminders,
    ), patch(
        "app.services.medication.medication_scheduler.MedicationRepository.find_active_by_ids",
        new_callable=AsyncMock,
        return_value=active_medications,
    ) as mock_find, _only_watch_stage_one():
        yield mock_find


@pytest.mark.asyncio
async def test_expired_course_does_not_expand_log(scheduler):
    """掛著藥、但當日一顆有效的都不剩：不展開紀錄，三階推播因此全部停下。"""
    now = datetime(2026, 9, 1, 8, 0, tzinfo=TAIPEI_TZ)
    reminder = _reminder_with_medications(["MED_1", "MED_2"])

    with _stage_one_with_active_medications([reminder], []), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
    ) as mock_upsert, patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.cancel_pending_by_reminder_ids",
        new_callable=AsyncMock,
        return_value=0,
    ):
        await scheduler.process_ticks(now=now)

    mock_upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_reminder_without_linked_medications_still_expands(scheduler):
    """本功能導入前建立的規則 medication_ids 是空陣列，行為必須與過去一致。

    要抑制的只有「掛了藥、但藥全部失效」，不是「沒掛藥」——把兩者混為一談會
    讓所有舊規則從此不再推播。
    """
    now = datetime(2026, 9, 1, 8, 0, tzinfo=TAIPEI_TZ)
    reminder = _reminder_with_medications([])

    with _stage_one_with_active_medications([reminder], []) as mock_find, patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
        return_value=(MagicMock(), True),
    ) as mock_upsert:
        await scheduler.process_ticks(now=now)

    mock_upsert.assert_awaited_once()
    # 沒有任何規則掛藥時，連藥品查詢都不該發出。
    mock_find.assert_not_awaited()


@pytest.mark.asyncio
async def test_partially_expired_course_still_expands(scheduler):
    """四顆藥只要還有一顆當日有效，這個時段就照常推播。"""
    now = datetime(2026, 9, 1, 8, 0, tzinfo=TAIPEI_TZ)
    reminder = _reminder_with_medications(["MED_1", "MED_2"])

    with _stage_one_with_active_medications(
        [reminder], [_active_medication("MED_2")]
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
        return_value=(MagicMock(), True),
    ) as mock_upsert:
        await scheduler.process_ticks(now=now)

    mock_upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_course_cancels_already_expanded_logs(scheduler):
    """當日已展開、還沒確認的紀錄要作廢，否則 T+20 與 T+30 仍會推空卡片。

    作廢範圍只到今天：更早的紀錄當時可能確實有藥，不該被回頭改寫。
    """
    now = datetime(2026, 9, 1, 8, 25, tzinfo=TAIPEI_TZ)
    reminder = _reminder_with_medications(["MED_1"])

    with _stage_one_with_active_medications([reminder], []), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
    ), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.cancel_pending_by_reminder_ids",
        new_callable=AsyncMock,
        return_value=1,
    ) as mock_cancel:
        await scheduler.process_ticks(now=now)

    mock_cancel.assert_awaited_once()
    args, kwargs = mock_cancel.await_args
    assert args[0] == ["REM_1"]
    assert kwargs["scheduled_from"] == datetime(2026, 9, 1, 0, 0, tzinfo=TAIPEI_TZ)


@pytest.mark.asyncio
async def test_medication_lookup_failure_does_not_suppress(scheduler):
    """藥品查詢失敗時不抑制任何規則。

    少推一張空卡片，與整批使用者漏掉一次真正該吃的藥相比，後者的代價高得多。
    """
    now = datetime(2026, 9, 1, 8, 0, tzinfo=TAIPEI_TZ)
    reminder = _reminder_with_medications(["MED_1"])

    reminder_prefix = (
        "app.services.medication.medication_scheduler.MedicationReminderRepository"
    )
    with patch(
        f"{reminder_prefix}.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[reminder],
    ), patch(
        "app.services.medication.medication_scheduler.MedicationRepository.find_active_by_ids",
        new_callable=AsyncMock,
        side_effect=RuntimeError("mongo down"),
    ), _only_watch_stage_one(), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
        return_value=(MagicMock(), True),
    ) as mock_upsert:
        await scheduler.process_ticks(now=now)

    mock_upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_misfire_log_only_on_first_creation(scheduler, caplog):
    """misfire 訊息只在本次才建立紀錄時印一次。

    is_misfired 每一輪都會對同一個時段重新算成 True；不看 created 就會變成
    每 60 秒重印一行。
    """
    import logging

    now = datetime(2026, 9, 1, 9, 0, tzinfo=TAIPEI_TZ)
    reminder = _reminder_with_medications([])

    reminder_prefix = (
        "app.services.medication.medication_scheduler.MedicationReminderRepository"
    )
    with patch(
        f"{reminder_prefix}.list_active_reminders_up_to_time",
        new_callable=AsyncMock,
        return_value=[reminder],
    ), _only_watch_stage_one(), patch(
        "app.services.medication.medication_scheduler.MedicationLogRepository.upsert_log",
        new_callable=AsyncMock,
        return_value=(MagicMock(), False),
    ), patch.object(
        scheduler, "_notify_missed_summary", new_callable=AsyncMock
    ):
        with caplog.at_level(logging.INFO):
            await scheduler.process_ticks(now=now)

    assert "Misfired slot recorded without push" not in caplog.text
