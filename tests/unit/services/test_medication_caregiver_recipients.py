"""用藥 T+30 逾時通報與停機彙整的收件人：照家庭授權的通知政策發給家屬。

本變更前只送給規則的建立者——長輩自己在 LIFF 設的提醒，逾時警報會推回長輩本人，
家屬一則都收不到。這裡釘住的是「名單從哪裡來、誰不在名單上、失敗怎麼退」。
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.medication import MedicationLog
from app.services.medication.medication_scheduler import MedicationScheduler

PATIENT = "U_PATIENT"


def _log(
    *, creator: str = PATIENT, user_id: str = PATIENT, log_id: str = "LOG_1"
) -> MedicationLog:
    scheduled_at = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)  # 台北 08:00
    return MedicationLog(
        id=log_id,
        reminder_id="REM_1",
        user_id=user_id,
        alert_notify_user_id=creator,
        slot_type="morning",
        scheduled_at=scheduled_at,
        timeout_at=scheduled_at,
        status="pending",
        patient_reminder_sent=True,
        urgent_reminder_sent=True,
    )


def _scheduler(recipients=None, *, push=None, profiles=None, authz=None):
    if authz is None:
        authz = MagicMock()
        authz.notification_recipients = AsyncMock(return_value=recipients or [])
    replier = MagicMock()
    replier.push_flex = push or AsyncMock(return_value=True)
    if profiles is None:
        profiles = MagicMock()
        profiles.get_user_profile = AsyncMock(return_value={"name": "李老先生"})
    reminders = MagicMock()
    reminders.find_by_ids = AsyncMock(return_value=[])
    medications = MagicMock()
    medications.find_active_by_ids = AsyncMock(return_value=[])
    scheduler = MedicationScheduler(
        replier=replier,
        user_profile_service=profiles,
        reminder_repository=reminders,
        log_repository=MagicMock(),
        medication_repository=medications,
        authorization_service=authz,
    )
    return scheduler, replier, authz


def _pushed_to(replier) -> list[str]:
    return [call.args[0] for call in replier.push_flex.await_args_list]


async def _alert(scheduler, log):
    return await scheduler._send_caregiver_alert(log, scheduler._medication_cache([log]))


# ── 名單從哪裡來 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reminder_the_elder_set_up_alerts_the_family_not_the_elder():
    """長輩自己設的提醒（建立者就是本人），逾時要通知家屬，而不是推回長輩。"""
    scheduler, replier, _ = _scheduler(["U_DAUGHTER", "U_SON"])

    sent = await _alert(scheduler, _log(creator=PATIENT))

    assert sent is True
    assert _pushed_to(replier) == ["U_DAUGHTER", "U_SON"]


@pytest.mark.asyncio
async def test_recipients_come_from_the_medication_missed_policy():
    scheduler, _, authz = _scheduler(["U_DAUGHTER"])

    await _alert(scheduler, _log())

    authz.notification_recipients.assert_awaited_once_with(PATIENT, "medication_missed")


@pytest.mark.asyncio
async def test_creator_who_is_no_longer_family_is_not_alerted():
    """建立者被移出家庭後，就不在名單上——名單看的是現在的族譜，不是當初誰建的。"""
    scheduler, replier, _ = _scheduler(["U_SON"])

    await _alert(scheduler, _log(creator="U_FORMER_CAREGIVER"))

    assert _pushed_to(replier) == ["U_SON"]


@pytest.mark.asyncio
async def test_patient_and_duplicates_are_dropped_from_the_list():
    scheduler, replier, _ = _scheduler([PATIENT, "U_DAUGHTER", "U_DAUGHTER", ""])

    await _alert(scheduler, _log())

    assert _pushed_to(replier) == ["U_DAUGHTER"]


# ── 送不到的時候 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_family_means_done_without_pushing():
    scheduler, replier, _ = _scheduler([])

    assert await _alert(scheduler, _log()) is True
    replier.push_flex.assert_not_awaited()


@pytest.mark.asyncio
async def test_lookup_failure_hands_the_claim_back_for_retry():
    """T+30 只送家屬，名單判定失敗若當成空名單，就是靜靜吞掉一則通報。"""
    authz = MagicMock()
    authz.notification_recipients = AsyncMock(side_effect=RuntimeError("mongo down"))
    scheduler, replier, _ = _scheduler(authz=authz)

    assert await _alert(scheduler, _log()) is False
    replier.push_flex.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_blocked_family_member_does_not_stop_the_others():
    """部分失敗算處理完：重試會讓已經收到的人再收一次。"""
    push = AsyncMock(side_effect=[RuntimeError("blocked the account"), True])
    scheduler, replier, _ = _scheduler(["U_BLOCKED", "U_SON"], push=push)

    assert await _alert(scheduler, _log()) is True
    assert _pushed_to(replier) == ["U_BLOCKED", "U_SON"]


@pytest.mark.asyncio
async def test_every_push_failing_hands_the_claim_back():
    scheduler, _, _ = _scheduler(
        ["U_DAUGHTER", "U_SON"], push=AsyncMock(return_value=False)
    )

    assert await _alert(scheduler, _log()) is False


@pytest.mark.asyncio
async def test_each_family_member_decides_for_themselves():
    """通知開關看的是收件人自己的設定：關掉的人不收，其他人照收。"""
    settings_by_user = {
        "U_OPTED_OUT": {"notify_family": False},
        "U_SON": {"notify_family": True},
    }
    profiles = MagicMock()
    profiles.get_user_profile = AsyncMock(
        side_effect=lambda uid: {"name": "李老先生", "settings": settings_by_user.get(uid, {})}
    )
    scheduler, replier, _ = _scheduler(["U_OPTED_OUT", "U_SON"], profiles=profiles)

    assert await _alert(scheduler, _log()) is True
    assert _pushed_to(replier) == ["U_SON"]


@pytest.mark.asyncio
async def test_everyone_opted_out_is_done_not_a_failure():
    profiles = MagicMock()
    profiles.get_user_profile = AsyncMock(
        return_value={"name": "李老先生", "settings": {"notify_family": False}}
    )
    scheduler, replier, _ = _scheduler(["U_SON"], profiles=profiles)

    assert await _alert(scheduler, _log()) is True
    replier.push_flex.assert_not_awaited()


# ── 停機期間錯過的時段 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missed_summary_is_grouped_per_family_member():
    """兩位長輩共用一位照顧者時，他只收一則彙整；各自的其他家屬各收各的。"""
    family = {"U_MOM": ["U_KID"], "U_DAD": ["U_KID", "U_UNCLE"]}
    authz = MagicMock()
    authz.notification_recipients = AsyncMock(
        side_effect=lambda patient, kind: family[patient]
    )
    scheduler, replier, _ = _scheduler(authz=authz)

    await scheduler._notify_missed_summary(
        [
            _log(user_id="U_MOM", log_id="L_MOM"),
            _log(user_id="U_DAD", log_id="L_DAD"),
        ]
    )

    assert sorted(_pushed_to(replier)) == ["U_KID", "U_UNCLE"]


@pytest.mark.asyncio
async def test_missed_summary_skips_a_patient_whose_family_cannot_be_resolved():
    """彙整是補充告知，不重試；一位長輩的名單查不到，不影響另一位。"""

    async def recipients(patient, kind):
        if patient == "U_MOM":
            raise RuntimeError("mongo down")
        return ["U_UNCLE"]

    authz = MagicMock()
    authz.notification_recipients = AsyncMock(side_effect=recipients)
    scheduler, replier, _ = _scheduler(authz=authz)

    await scheduler._notify_missed_summary(
        [
            _log(user_id="U_MOM", log_id="L_MOM"),
            _log(user_id="U_DAD", log_id="L_DAD"),
        ]
    )

    assert _pushed_to(replier) == ["U_UNCLE"]
