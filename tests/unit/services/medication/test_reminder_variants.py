"""用藥提醒兩台拉霸的核心規則：誰能收到哪些語氣、什麼算成功、怎麼挑。

挑選用階層式 Thompson sampling：每個選項先以「其他長輩」的成功率當起點
（份量相當於 10 次提醒），再疊上這位長輩自己的紀錄。測試用「取期望值」的
抽樣函式讓挑選結果可預期；只有探索那一條用真亂數，驗的是「不會永遠挑同一個」。
"""

import random
from datetime import datetime, timedelta, timezone

import pytest

from app.models.medication import MedicationLog
from app.services.medication.reminder_variants import (
    ReminderVariant,
    arm_counts,
    choose_arm,
    choose_variant,
    eligible_tones,
    outcome_of,
)

TIMEOUT = datetime(2026, 9, 14, 0, 30, tzinfo=timezone.utc)


def _log(
    *,
    user_id="U_PATIENT",
    alert_notify_user_id="U_CARE",
    status="pending",
    taken_at=None,
    reminder_tone=None,
    nudge_minutes=None,
):
    return MedicationLog(
        reminder_id="REM_1",
        user_id=user_id,
        alert_notify_user_id=alert_notify_user_id,
        slot_type="morning",
        scheduled_at=TIMEOUT - timedelta(minutes=30),
        timeout_at=TIMEOUT,
        status=status,
        taken_at=taken_at,
        reminder_tone=reminder_tone,
        nudge_minutes=nudge_minutes,
    )


def _mean(alpha, beta):
    """以期望值代替抽樣，讓挑選結果可預期。"""
    return alpha / (alpha + beta)


# ── 誰能收到哪些語氣 ────────────────────────────────────────────────


def test_family_tone_is_offered_when_someone_else_gets_the_alert():
    assert eligible_tones(_log(alert_notify_user_id="U_CARE")) == ("control", "family", "brief")


def test_family_tone_is_not_offered_for_self_managed_reminders():
    """長輩自己設的提醒，逾時通報的對象就是他自己，「家人就知道」不成立。"""
    assert eligible_tones(_log(alert_notify_user_id="U_PATIENT")) == ("control", "brief")


# ── 什麼算成功 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "taken_at", "expected"),
    [
        ("taken", TIMEOUT - timedelta(minutes=1), True),
        ("taken", TIMEOUT, True),
        # 家屬已經被通知之後才按：提醒沒有在時限內奏效
        ("taken", TIMEOUT + timedelta(minutes=5), False),
        ("missed", None, False),
        # 還沒走完，或規則被關掉：沒有結果可學
        ("pending", None, None),
        ("cancelled", None, None),
    ],
)
def test_outcome_is_confirming_before_the_family_alert(status, taken_at, expected):
    assert outcome_of(_log(status=status, taken_at=taken_at)) is expected


def test_outcome_compares_naive_utc_read_back_from_mongo():
    """pymongo 讀回來的時間是 naive UTC，與帶時區的 timeout 比較不能出錯。"""
    log = _log(status="taken", taken_at=datetime(2026, 9, 14, 0, 29))
    log.timeout_at = datetime(2026, 9, 14, 0, 30)
    assert outcome_of(log) is True


# ── 計數：這位長輩 vs 其他人 ─────────────────────────────────────────


def test_arm_counts_separate_this_user_from_everyone_else():
    logs = [
        _log(user_id="U_A", status="missed", reminder_tone="brief"),
        _log(user_id="U_A", status="taken", taken_at=TIMEOUT, reminder_tone="brief"),
        _log(user_id="U_B", status="taken", taken_at=TIMEOUT, reminder_tone="brief"),
        _log(user_id="U_B", status="taken", taken_at=TIMEOUT, reminder_tone="control"),
        # 沒有結果、或不在這台拉霸裡的紀錄不計
        _log(user_id="U_B", status="pending", reminder_tone="control"),
        _log(user_id="U_B", status="missed", reminder_tone=None),
    ]

    others, mine = arm_counts(logs, lambda log: log.reminder_tone, user_id="U_A")

    assert mine == {"brief": (1, 1)}
    assert others == {"brief": (1, 0), "control": (1, 0)}


# ── 怎麼挑 ──────────────────────────────────────────────────────────


def test_population_evidence_sets_the_starting_point():
    others = {"control": (10, 40), "brief": (40, 10)}
    assert choose_arm(("control", "brief"), others, {}, sample=_mean) == "brief"


def test_a_few_of_a_users_own_reminders_do_not_overturn_strong_population_evidence():
    """全體起點相當於 10 次提醒：自己的 3 次不足以推翻它。"""
    others = {"control": (10, 40), "brief": (40, 10)}
    mine = {"control": (3, 0), "brief": (0, 3)}
    assert choose_arm(("control", "brief"), others, mine, sample=_mean) == "brief"


def test_enough_of_a_users_own_reminders_override_the_population():
    others = {"control": (10, 40), "brief": (40, 10)}
    mine = {"control": (12, 0), "brief": (0, 12)}
    assert choose_arm(("control", "brief"), others, mine, sample=_mean) == "control"


def test_only_offered_arms_can_be_chosen():
    """家人版對自己設提醒的長輩不開放，即使全體資料上它最好。"""
    others = {"family": (50, 0), "control": (0, 50), "brief": (0, 50)}
    assert choose_arm(("control", "brief"), others, {}, sample=_mean) in ("control", "brief")


def test_choose_variant_picks_each_bandit_from_its_own_evidence():
    """語氣看 reminder_tone、時機看 nudge_minutes，兩台各自計數。"""
    history = [
        _log(user_id="U_OTHER", status="taken", taken_at=TIMEOUT, reminder_tone="brief", nudge_minutes=10)
        for _ in range(30)
    ] + [
        _log(user_id="U_OTHER", status="missed", reminder_tone="control", nudge_minutes=20)
        for _ in range(30)
    ]

    variant = choose_variant(_log(), history, sample=_mean)

    assert variant == ReminderVariant(tone="brief", nudge_minutes=10)


def test_choose_variant_never_offers_family_for_self_managed_reminders():
    history = [
        _log(user_id="U_OTHER", status="taken", taken_at=TIMEOUT, reminder_tone="family", nudge_minutes=20)
        for _ in range(30)
    ]

    variant = choose_variant(_log(alert_notify_user_id="U_PATIENT"), history, sample=_mean)

    assert variant.tone != "family"


def test_without_data_every_arm_still_gets_tried():
    """Thompson sampling 靠抽樣探索：沒有資料時三個選項都要輪得到。"""
    sample = random.SystemRandom().betavariate
    chosen = {choose_arm((20, 15, 10), {}, {}, sample=sample) for _ in range(300)}
    assert chosen == {20, 15, 10}
