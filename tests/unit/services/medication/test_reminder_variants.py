"""用藥提醒兩台拉霸的核心規則：誰能收到哪些語氣、什麼算成功、怎麼計數、怎麼挑。

挑選用階層式 Thompson sampling：每個選項先以「同一群的其他長輩」的成功率當起點
（份量相當於 10 次提醒），再疊上這位長輩自己的紀錄。語氣的「同一群」依提醒是
誰設的分開算：家人版只會出現在家屬設的提醒上，兩群長輩原本的準時率若不同，混在
一起算就會把這個差距當成語氣的效果。

測試用「取期望值」的抽樣函式讓挑選結果可預期；只有探索那一條用真亂數，驗的是
「不會永遠挑同一個」。
"""

import random
from datetime import datetime, timedelta, timezone

import pytest

from app.models.medication import MedicationLog
from app.services.medication.reminder_variants import (
    ReminderVariant,
    build_variant_stats,
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


def _won(user_id, tone, nudge_minutes, alert_notify_user_id="U_CARE"):
    return _log(
        user_id=user_id, alert_notify_user_id=alert_notify_user_id, status="taken",
        taken_at=TIMEOUT, reminder_tone=tone, nudge_minutes=nudge_minutes,
    )


def _lost(user_id, tone, nudge_minutes, alert_notify_user_id="U_CARE"):
    return _log(
        user_id=user_id, alert_notify_user_id=alert_notify_user_id, status="missed",
        reminder_tone=tone, nudge_minutes=nudge_minutes,
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


# ── 計數：每一輪彙整一次 ─────────────────────────────────────────────


def test_stats_count_tone_per_group_and_per_user_and_nudge_per_user():
    stats = build_variant_stats(
        [
            _lost("U_A", "brief", 10),
            _won("U_A", "brief", 10),
            _won("U_B", "brief", 15),
            _won("U_B", "control", 20, alert_notify_user_id="U_B"),
            # 時機被清掉的一頓：語氣照算、時機不算
            _won("U_B", "family", None),
            # 沒有結果、或不在拉霸裡的紀錄不計
            _log(user_id="U_B", status="pending", reminder_tone="control", nudge_minutes=20),
            _lost("U_B", None, None),
        ]
    )

    assert stats.tone == {
        (True, "brief"): (2, 1),
        (False, "control"): (1, 0),
        (True, "family"): (1, 0),
    }
    assert stats.tone_by_user == {
        ("U_A", True, "brief"): (1, 1),
        ("U_B", True, "brief"): (1, 0),
        ("U_B", False, "control"): (1, 0),
        ("U_B", True, "family"): (1, 0),
    }
    assert stats.nudge == {10: (1, 1), 15: (1, 0), 20: (1, 0)}
    assert stats.nudge_by_user == {("U_A", 10): (1, 1), ("U_B", 15): (1, 0), ("U_B", 20): (1, 0)}


# ── choose_arm：起點與個人證據 ───────────────────────────────────────


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


def test_without_data_every_arm_still_gets_tried():
    """Thompson sampling 靠抽樣探索：沒有資料時三個選項都要輪得到。"""
    sample = random.SystemRandom().betavariate
    chosen = {choose_arm((20, 15, 10), {}, {}, sample=sample) for _ in range(300)}
    assert chosen == {20, 15, 10}


# ── choose_variant：兩台各自學 ───────────────────────────────────────


def test_choose_variant_picks_each_bandit_from_its_own_evidence():
    stats = build_variant_stats(
        [_won("U_OTHER", "brief", 10) for _ in range(30)]
        + [_lost("U_OTHER", "control", 20) for _ in range(30)]
    )

    variant = choose_variant(_log(), stats, sample=_mean)

    assert variant == ReminderVariant(tone="brief", nudge_minutes=10)


def test_choose_variant_never_offers_family_for_self_managed_reminders():
    stats = build_variant_stats([_won("U_OTHER", "family", 20) for _ in range(30)])

    variant = choose_variant(_log(alert_notify_user_id="U_PATIENT"), stats, sample=_mean)

    assert variant.tone != "family"


def test_tone_starting_point_comes_only_from_the_same_kind_of_reminder():
    """家屬設的提醒上 brief 很有效，不代表長輩自己設的提醒也是；後者的起點只看
    同樣是自己設提醒的長輩。"""
    stats = build_variant_stats(
        [_won("U_FAM", "brief", 20) for _ in range(40)]
        + [_lost("U_FAM", "control", 20) for _ in range(40)]
        + [_lost("U_SELF", "brief", 20, alert_notify_user_id="U_SELF") for _ in range(10)]
        + [_won("U_SELF", "control", 20, alert_notify_user_id="U_SELF") for _ in range(5)]
        + [_lost("U_SELF", "control", 20, alert_notify_user_id="U_SELF") for _ in range(5)]
    )

    # 只看自己設提醒的那群：brief 0 勝 10 敗、control 5 勝 5 敗 → control。
    # 若兩群混在一起：brief 40 勝 10 敗、control 5 勝 45 敗 → 會挑 brief。
    variant = choose_variant(_log(alert_notify_user_id="U_PATIENT"), stats, sample=_mean)

    assert variant.tone == "control"


def test_a_users_own_history_does_not_count_twice():
    """全體起點排除這位長輩自己的紀錄：他的紀錄會以個人證據再疊一次。"""
    stats = build_variant_stats(
        [_won("U_PATIENT", "brief", 20) for _ in range(10)]
        + [_lost("U_OTHER", "brief", 20) for _ in range(2)]
        + [_won("U_OTHER", "control", 20) for _ in range(7)]
        + [_lost("U_OTHER", "control", 20) for _ in range(3)]
    )

    # 正確：brief 起點只看其他人 0 勝 2 敗 = 0.25，疊上自己 10 勝 → 12.5/20 = 0.625；
    #       control 起點 8/12 ≈ 0.667 → 挑 control。
    # 算兩次：brief 起點變成 10 勝 2 敗 ≈ 0.786，再疊自己 10 勝 → ≈ 0.893 → 會挑 brief。
    variant = choose_variant(_log(), stats, sample=_mean)
    assert variant.tone == "control"


def test_timing_is_left_out_when_the_scheduler_says_so():
    """T+0 晚送的那一頓只進語氣拉霸：時機回傳 None，由排程器維持原本的 +20。"""
    stats = build_variant_stats([_won("U_OTHER", "brief", 10) for _ in range(30)])

    variant = choose_variant(_log(), stats, sample=_mean, apply_timing=False)

    assert variant == ReminderVariant(tone="brief", nudge_minutes=None)
