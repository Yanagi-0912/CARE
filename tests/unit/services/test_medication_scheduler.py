from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.models.medication import TAIPEI_TZ, MedicationLog, MedicationReminder
from app.services.medication.medication_scheduler import MedicationScheduler


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
# 這裡刻意不透過 process_ticks 走完整流程來驗證，而是直接呼叫
# `_resolve_medication_names`：它是新增邏輯唯一讀 medication_ids 的地方，
# 且用 collection= 注入假的 collection，不需要 monkeypatch 掉
# MedicationReminderRepository／MedicationRepository 這兩個 static class。


def _fake_collection(*, find_one_return=None, find_return=None):
    """建立一個假的 Motor collection：find_one 回傳單一文件，find(...).to_list(...) 回傳清單。"""
    col = MagicMock()
    col.find_one = AsyncMock(return_value=find_one_return)
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=find_return or [])
    col.find = MagicMock(return_value=cursor)
    return col


@pytest.mark.asyncio
async def test_resolve_medication_names_empty_for_legacy_reminder_without_field(scheduler):
    """
    本變更前建立的規則在資料庫中沒有 medication_ids 欄位；讀回後視為空陣列，
    解析結果應為空清單，且完全不必查詢藥品表。
    """
    reminder_doc = {
        "_id": "REM_1",
        "creator_user_id": "U_CARE",
        "user_id": "U_PATIENT",
        "slot_type": "morning",
        "scheduled_time": "08:00",
        "start_date": "2026-07-29",
        "enabled": True,
        # 沒有 medication_ids 鍵，模擬既有規則
    }
    reminder_col = _fake_collection(find_one_return=reminder_doc)
    medication_col = _fake_collection(find_return=[])

    names = await scheduler._resolve_medication_names(
        "REM_1",
        datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc),
        reminder_collection=reminder_col,
        medication_collection=medication_col,
    )

    assert names == []
    medication_col.find.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_medication_names_lists_active_drugs(scheduler):
    reminder_doc = {
        "_id": "REM_1",
        "creator_user_id": "U_CARE",
        "user_id": "U_PATIENT",
        "slot_type": "morning",
        "scheduled_time": "08:00",
        "medication_ids": ["M1", "M2"],
    }
    medication_docs = [
        {"_id": "M1", "user_id": "U_PATIENT", "created_by_user_id": "U_CARE", "name": "脈優"},
        {"_id": "M2", "user_id": "U_PATIENT", "created_by_user_id": "U_CARE", "name": "利尿劑"},
    ]
    reminder_col = _fake_collection(find_one_return=reminder_doc)
    medication_col = _fake_collection(find_return=medication_docs)

    names = await scheduler._resolve_medication_names(
        "REM_1",
        datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc),
        reminder_collection=reminder_col,
        medication_collection=medication_col,
    )

    assert names == ["脈優", "利尿劑"]
    (query,), _ = medication_col.find.call_args
    # 委派給 find_active_by_ids 既有的有效性判定（enabled + 療程日期區間），
    # 不在 scheduler 這一層重做一套過濾邏輯。
    assert query["_id"] == {"$in": ["M1", "M2"]}
    assert query["enabled"] is True


@pytest.mark.asyncio
async def test_resolve_medication_names_excludes_drug_filtered_out_by_repository(scheduler):
    """
    藥品失效（停用或療程已結束）時，find_active_by_ids 就不會把它撈出來；
    這裡驗證 scheduler 老實把查詢結果轉成名稱清單，失效藥品不會出現。
    """
    reminder_doc = {
        "_id": "REM_1",
        "creator_user_id": "U_CARE",
        "user_id": "U_PATIENT",
        "slot_type": "morning",
        "scheduled_time": "08:00",
        "medication_ids": ["M1", "M2"],
    }
    # M2 已停用／過期，模擬 find_active_by_ids 在 DB 端就把它濾掉
    medication_docs = [
        {"_id": "M1", "user_id": "U_PATIENT", "created_by_user_id": "U_CARE", "name": "脈優"},
    ]
    reminder_col = _fake_collection(find_one_return=reminder_doc)
    medication_col = _fake_collection(find_return=medication_docs)

    names = await scheduler._resolve_medication_names(
        "REM_1",
        datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc),
        reminder_collection=reminder_col,
        medication_collection=medication_col,
    )

    assert names == ["脈優"]


@pytest.mark.asyncio
async def test_resolve_medication_names_missing_reminder_returns_empty(scheduler):
    reminder_col = _fake_collection(find_one_return=None)
    medication_col = _fake_collection(find_return=[])

    names = await scheduler._resolve_medication_names(
        "REM_MISSING",
        datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc),
        reminder_collection=reminder_col,
        medication_collection=medication_col,
    )

    assert names == []
    medication_col.find.assert_not_called()


def test_process_ticks_expansion_path_does_not_reference_medication_ids():
    """
    展開與搶佔路徑不得讀 medication_ids —— 這是排程器既有併發保證（原子搶佔、
    唯一索引、停機補償）能夠成立的前提，見 openspec 決策。藥名解析已刻意搬到
    `_resolve_medication_names`，只在組裝推播文案時才呼叫；這裡用原始碼掃描
    鎖住 process_ticks 本體不會再讀這個欄位。
    """
    import inspect

    source = inspect.getsource(MedicationScheduler.process_ticks)
    assert "medication_ids" not in source

