"""推播權租約、送達標記、額度用完的不計次還原、重新啟用時刻、提交回滾。

對應的修正：
- 搶佔與推播之間 pod 死掉（OOM／SIGKILL／task 取消）旗標永遠停在 True；
- LINE 429 讓每一頓都被記成「已送出」、30 分鐘後家屬收到假的漏服警報；
- 重新啟用一筆規則會為當天已經過去的時段補建漏服；
- 藥袋提交在 create_many 之後失敗，重試會插第二份藥。
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.medication import MedicationReminder
from app.repositories.medication_repository import (
    MedicationLogRepository,
    MedicationReminderRepository,
    MedicationRepository,
)
from app.repositories.push_claim import (
    MAX_PUSH_ATTEMPTS,
    PUSH_CLAIM_LEASE,
    claimable_filter,
    give_up_push_claim,
    mark_push_stage,
    release_push_claim,
)

NOW = datetime(2026, 9, 16, 0, 5, tzinfo=timezone.utc)


@pytest.fixture()
def logs_col(monkeypatch):
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1, matched_count=1))
    col.find_one_and_update = AsyncMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])
    col.find = MagicMock(return_value=cursor)
    monkeypatch.setattr(
        "app.repositories.medication_repository.MongoDBManager.get_medication_logs_collection",
        lambda: col,
    )
    return col


# ── push_claim 的共用原語 ─────────────────────────────────────────────


def test_claimable_filter_excludes_legacy_sent_docs_and_given_up_docs():
    """租約接手的分支必須同時要求「有 claimed_at 且早於租約」與「沒放棄過」：
    本欄位落地前已經送達的舊紀錄沒有 claimed_at（比較運算子對缺欄位不成立，
    不會被接手），放棄的紀錄旗標同樣停在 True，少了嘗試次數這一項會在 5 分鐘
    後被無限重來。"""
    query = claimable_filter(
        sent_field="s",
        sent_at_field="s_at",
        skipped_at_field="sk_at",
        claimed_at_field="c_at",
        attempts_field="n",
        now=NOW,
        fresh={"status": "pending"},
        stale={"status": "missed"},
    )
    fresh, stale = query["$or"]
    assert fresh == {"s": False, "status": "pending"}
    assert stale == {
        "s": True,
        "s_at": None,
        "sk_at": None,
        "c_at": {"$lt": NOW - PUSH_CLAIM_LEASE},
        "n": {"$not": {"$gte": MAX_PUSH_ATTEMPTS}},
        "status": "missed",
    }


def test_lease_is_five_minutes():
    """5 分鐘：遠大於一次搶佔到推播完成的秒級壽命，等於 MAX_PUSH_ATTEMPTS × 60 秒
    的瞬時故障視窗，且小於 T+20 到 T+30 的 10 分鐘（死掉的 T+0 會在下一階段之前
    被接手）。改動這個值要連同 push_claim.py 的說明一起改。"""
    assert PUSH_CLAIM_LEASE == timedelta(minutes=5)
    assert PUSH_CLAIM_LEASE >= timedelta(seconds=60) * MAX_PUSH_ATTEMPTS
    assert PUSH_CLAIM_LEASE < timedelta(minutes=10)


@pytest.mark.asyncio
async def test_release_without_counting_only_clears_the_flag():
    """LINE 429：不是這一則的失敗，不累加嘗試次數，只把旗標還回去並記下原因。
    照常累加的話，五個 tick 之後所有待送的提醒都會被放棄，額度恢復也不補送。"""
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    col.find_one_and_update = AsyncMock()

    released = await release_push_claim(
        col,
        "L1",
        stage="T+0",
        sent_field="patient_reminder_sent",
        attempts_field="patient_reminder_attempts",
        count_attempt=False,
        error="quota_exceeded",
    )

    assert released is True
    col.find_one_and_update.assert_not_awaited()
    (query, update), _ = col.update_one.call_args
    assert query == {"_id": "L1", "patient_reminder_sent": True}
    assert update == {
        "$set": {"patient_reminder_sent": False, "last_push_error": "quota_exceeded"}
    }


@pytest.mark.asyncio
async def test_release_with_counting_records_the_error_alongside_the_increment():
    col = MagicMock()
    col.find_one_and_update = AsyncMock(
        return_value={"_id": "L1", "patient_reminder_attempts": 1}
    )
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

    await release_push_claim(
        col,
        "L1",
        stage="T+0",
        sent_field="patient_reminder_sent",
        attempts_field="patient_reminder_attempts",
        error="transient",
    )

    (_, update), _ = col.find_one_and_update.call_args
    assert update == {
        "$inc": {"patient_reminder_attempts": 1},
        "$set": {"last_push_error": "transient"},
    }


@pytest.mark.asyncio
async def test_give_up_sets_attempts_to_the_cap_and_keeps_the_flag():
    """400／404：同樣的內容再送四次只是白花四次，直接寫成上限；旗標不動，
    租約也不會接手（claimable_filter 排除已達上限者）。"""
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

    assert await give_up_push_claim(
        col, "L1", stage="T+0", attempts_field="patient_reminder_attempts", error="rejected"
    )

    (query, update), _ = col.update_one.call_args
    assert query == {"_id": "L1"}
    assert update == {
        "$set": {"patient_reminder_attempts": MAX_PUSH_ATTEMPTS, "last_push_error": "rejected"}
    }


@pytest.mark.asyncio
async def test_mark_push_stage_writes_the_timestamp_unconditionally():
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

    assert await mark_push_stage(col, "L1", field="patient_reminder_sent_at", now=NOW)

    (query, update), _ = col.update_one.call_args
    assert query == {"_id": "L1"}
    assert update == {"$set": {"patient_reminder_sent_at": NOW}}


# ── MedicationLogRepository：三個階段的租約與標記 ─────────────────────


@pytest.mark.asyncio
async def test_mark_stage_sent_and_skipped_target_the_stage_fields(logs_col):
    await MedicationLogRepository.mark_stage_sent("L1", "urgent_reminder", now=NOW)
    (_, update), _ = logs_col.update_one.call_args
    assert update == {"$set": {"urgent_reminder_sent_at": NOW}}

    await MedicationLogRepository.mark_stage_skipped("L1", "caregiver_alert", now=NOW)
    (_, update), _ = logs_col.update_one.call_args
    assert update == {"$set": {"caregiver_alert_skipped_at": NOW}}


@pytest.mark.asyncio
async def test_give_up_stage_uses_the_stage_attempts_field(logs_col):
    await MedicationLogRepository.give_up_stage("L1", "caregiver_alert", "rejected")
    (_, update), _ = logs_col.update_one.call_args
    assert update["$set"]["caregiver_alert_attempts"] == MAX_PUSH_ATTEMPTS
    assert update["$set"]["last_push_error"] == "rejected"


def test_unknown_stage_is_rejected():
    with pytest.raises(ValueError):
        MedicationLogRepository._stage_fields("bogus")


@pytest.mark.asyncio
async def test_urgent_claim_requires_t0_to_have_been_delivered(logs_col):
    """T+0 從未送達（額度用完、搶佔者死掉）的那一頓不該被催「您尚未點擊我已用藥」。"""
    await MedicationLogRepository.claim_patient_urgent_reminder("L1", now=NOW)
    (query, update), _ = logs_col.update_one.call_args
    fresh, stale = query["$or"]
    assert fresh == {
        "status": "pending",
        "urgent_reminder_sent": False,
        "patient_reminder_sent_at": {"$ne": None},
    }
    assert stale["patient_reminder_sent_at"] == {"$ne": None}
    assert stale["urgent_reminder_claimed_at"] == {"$lt": NOW - PUSH_CLAIM_LEASE}
    assert update == {
        "$set": {"urgent_reminder_sent": True, "urgent_reminder_claimed_at": NOW}
    }


@pytest.mark.asyncio
async def test_caregiver_claim_lease_branch_reclaims_a_dead_missed_claim(logs_col):
    """第一次搶佔已經把 status 改成 missed；死掉的搶佔留下的就是這個形狀，
    接手的分支要看 missed 而不是 pending，但仍不能接手 taken。"""
    await MedicationLogRepository.claim_caregiver_alert("L1", now=NOW)
    (query, _), _ = logs_col.update_one.call_args
    _, stale = query["$or"]
    assert stale["status"] == "missed"
    assert stale["caregiver_alert_sent"] is True
    assert stale["caregiver_alert_sent_at"] is None
    assert stale["caregiver_alert_skipped_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name",
    ["list_pending_urgent_reminders", "list_pending_caregiver_alerts"],
)
async def test_later_stages_only_list_logs_whose_t0_was_delivered(logs_col, method_name):
    """LINE 額度用完的那個月：T+0 旗標在搶佔時就設起，但沒有 sent_at。少了這個
    條件，30 分鐘後每一頓都變成「他漏吃了」的家屬警報，而他一則都沒收到。"""
    await getattr(MedicationLogRepository, method_name)(threshold_time=NOW)
    (query,), _ = logs_col.find.call_args
    assert query["patient_reminder_sent_at"] == {"$ne": None}
    assert query["status"] == "pending"


@pytest.mark.asyncio
async def test_release_patient_reminder_passes_quota_semantics_through(logs_col):
    await MedicationLogRepository.release_patient_reminder(
        "L1", count_attempt=False, error="quota_exceeded"
    )
    logs_col.find_one_and_update.assert_not_awaited()
    (query, update), _ = logs_col.update_one.call_args
    assert query == {"_id": "L1", "patient_reminder_sent": True}
    assert update["$set"] == {
        "patient_reminder_sent": False,
        "last_push_error": "quota_exceeded",
    }


@pytest.mark.asyncio
async def test_release_caregiver_alert_without_counting_still_restores_pending(logs_col):
    """T+30 的額度用完：旗標與 status 一起還原（missed → pending），且只在仍為
    missed 時——與計次版同一個防呆。"""
    await MedicationLogRepository.release_caregiver_alert(
        "L1", count_attempt=False, error="quota_exceeded"
    )
    (query, update), _ = logs_col.update_one.call_args
    assert query == {"_id": "L1", "caregiver_alert_sent": True, "status": "missed"}
    assert update["$set"] == {
        "caregiver_alert_sent": False,
        "status": "pending",
        "last_push_error": "quota_exceeded",
    }


@pytest.mark.asyncio
async def test_mark_as_taken_keeps_taken_at_and_adds_wall_clock_confirmed_at(logs_col):
    """taken_at 沿用呼叫端給的值，confirmed_at 一律是現在——兩者在「隔天才按
    昨天的卡片」時分得出來。刻意不改寫 taken_at：拉霸的準時判定與服藥狀況查詢
    都讀它。"""
    slot_time = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
    logs_col.find_one = AsyncMock(
        return_value={
            "_id": "L1",
            "reminder_id": "R1",
            "user_id": "U",
            "alert_notify_user_id": "U",
            "slot_type": "morning",
            "scheduled_at": slot_time,
            "timeout_at": slot_time,
            "status": "taken",
            "taken_at": slot_time,
        }
    )
    before = datetime.now(tz=timezone.utc)

    await MedicationLogRepository.mark_as_taken("L1", taken_at=slot_time)

    (_, update), _ = logs_col.update_one.call_args
    assert update["$set"]["taken_at"] == slot_time
    assert update["$set"]["confirmed_at"] >= before


# ── MedicationReminderRepository：重新啟用時刻 ────────────────────────


@pytest.mark.asyncio
async def test_create_reminder_sets_enabled_at_to_created_at(monkeypatch):
    col = MagicMock()
    col.insert_one = AsyncMock()
    monkeypatch.setattr(
        "app.repositories.medication_repository.MongoDBManager.get_medication_reminders_collection",
        lambda: col,
    )
    created_at = datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc)
    reminder = MedicationReminder(
        creator_user_id="U", user_id="U", slot_type="morning", created_at=created_at
    )

    saved = await MedicationReminderRepository.create_reminder(reminder)

    (doc,), _ = col.insert_one.call_args
    assert doc["enabled_at"] == created_at
    assert saved.enabled_at == created_at


@pytest.mark.asyncio
async def test_find_or_create_stamps_enabled_at_on_insert_and_on_reactivation():
    """新插入的規則 enabled_at = 現在；命中一筆停用的規則並復活它時同樣刷新
    ——今天 20:00 復活的規則，早上 08:00 不能被補成漏服。"""
    disabled = {
        "_id": "R_OLD",
        "creator_user_id": "U",
        "user_id": "U",
        "slot_type": "morning",
        "scheduled_time": "08:00",
        "enabled": False,
        "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
    }
    col = MagicMock()
    col.find_one_and_update = AsyncMock(
        side_effect=[disabled, {**disabled, "enabled": True}]
    )

    _, reactivated = await MedicationReminderRepository.find_or_create_reminder(
        user_id="U", slot_type="morning", creator_user_id="U",
        scheduled_time="08:00", collection=col,
    )

    assert reactivated is True
    first, second = col.find_one_and_update.call_args_list
    set_on_insert = first.args[1]["$setOnInsert"]
    assert set_on_insert["enabled_at"] == set_on_insert["created_at"]
    fix = second.args[1]["$set"]
    assert fix["enabled"] is True
    assert isinstance(fix["enabled_at"], datetime)


@pytest.mark.asyncio
async def test_update_reminder_stamps_enabled_at_only_when_turning_on():
    """enabled=True 先做一次條件式寫入（原本不是開著的才刷新 enabled_at），
    再做主更新；重送 enabled=True 原值（LIFF 整份儲存）不算重新啟用，靠的是
    filter 裡的 `enabled: {$ne: True}`，不是先讀再判斷。"""
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    col.find_one = AsyncMock(
        return_value={
            "_id": "R1", "creator_user_id": "U", "user_id": "U",
            "slot_type": "morning", "scheduled_time": "08:00", "enabled": True,
        }
    )

    await MedicationReminderRepository.update_reminder("R1", {"enabled": True}, collection=col)

    stamp, main = col.update_one.call_args_list
    assert stamp.args[0] == {"_id": "R1", "enabled": {"$ne": True}}
    assert set(stamp.args[1]["$set"]) == {"enabled_at"}
    assert main.args[1]["$set"]["enabled"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("update_data", [{"enabled": False}, {"scheduled_time": "09:00"}])
async def test_update_reminder_does_not_touch_enabled_at_otherwise(update_data):
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    col.find_one = AsyncMock(
        return_value={
            "_id": "R1", "creator_user_id": "U", "user_id": "U",
            "slot_type": "morning", "scheduled_time": "08:00", "enabled": False,
        }
    )

    await MedicationReminderRepository.update_reminder("R1", update_data, collection=col)

    assert col.update_one.await_count == 1
    assert "enabled_at" not in col.update_one.call_args.args[1]["$set"]


def test_reminder_without_enabled_at_reads_back_as_none():
    """本欄位落地前的規則：讀回 None，排程器退回只看 created_at。"""
    reminder = MedicationReminder(
        **{"_id": "R1", "creator_user_id": "U", "user_id": "U", "slot_type": "morning"}
    )
    assert reminder.enabled_at is None


# ── MedicationRepository.delete_by_ids：提交回滾 ─────────────────────


@pytest.mark.asyncio
async def test_delete_by_ids_deletes_exactly_those_ids():
    col = MagicMock()
    col.delete_many = AsyncMock(return_value=MagicMock(deleted_count=2))

    assert await MedicationRepository.delete_by_ids(["M1", "M2"], collection=col) == 2

    col.delete_many.assert_awaited_once_with({"_id": {"$in": ["M1", "M2"]}})


@pytest.mark.asyncio
async def test_delete_by_ids_with_empty_list_does_not_touch_the_database():
    col = MagicMock()
    col.delete_many = AsyncMock()

    assert await MedicationRepository.delete_by_ids([], collection=col) == 0

    col.delete_many.assert_not_awaited()
