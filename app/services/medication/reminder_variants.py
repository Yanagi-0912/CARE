"""用藥提醒的兩台拉霸：這一頓用哪種語氣說、T+20 催促改在幾分鐘後送。

為什麼用拉霸：每位長輩對提醒的反應不同，而且每天都有好幾次提醒、30 分鐘內就
知道有沒有按「已服用」——決策多、回饋快，適合一邊試一邊學。只換訊息的說法與
催促時機，不動服藥時間，也不動 T+30 的家屬通報，所以探索時挑到效果較差的選項，
最壞只是那一次提醒沒那麼有效。

兩台各自學，不合成 9 種組合：兩台從同一批提醒學，每台 3 個選項，需要的資料約是
9 種組合的三分之一；代價是假設語氣與催促時機之間沒有交互作用。

挑選用階層式 Thompson sampling（先大家、再個人）：每個選項先以「其他長輩」的
成功率當起點，份量相當於 PRIOR_WEIGHT 次提醒，再疊上這位長輩自己的成功與失敗
次數，從這個 Beta 分佈抽一個值，抽到最大的選項勝出。抽樣本身就是探索：資料少的
選項分佈寬，偶爾會抽到高值而被試到。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Hashable, Iterable, Mapping, Optional, Sequence

from app.models.medication import MedicationLog, ensure_aware_utc

TONE_CONTROL = "control"
TONE_FAMILY = "family"
TONE_BRIEF = "brief"
TONES: tuple[str, ...] = (TONE_CONTROL, TONE_FAMILY, TONE_BRIEF)

# 催促在最晚服藥時刻之後幾分鐘送。20 是上線前的固定值（對照組）。T+30 的家屬
# 通報不動，所以選項最晚只到 20：再晚，長輩收到催促後只剩不到 10 分鐘可以回應。
NUDGE_CONTROL = 20
NUDGE_MINUTES: tuple[int, ...] = (NUDGE_CONTROL, 15, 10)

# 其他長輩的成功率當起點時，份量相當於幾次提醒。一位長輩自己累積到這個次數
# （例如一天 3 次，約 3 天），他自己的反應就與全體同重；在那之前以全體為主，
# 前幾天才不會因為一兩次沒按就把某個選項打入冷宮。
PRIOR_WEIGHT = 10.0

ArmCounts = dict[Hashable, tuple[int, int]]
"""{選項: (成功次數, 失敗次數)}"""


@dataclass(frozen=True)
class ReminderVariant:
    tone: str
    nudge_minutes: int


CONTROL_VARIANT = ReminderVariant(tone=TONE_CONTROL, nudge_minutes=NUDGE_CONTROL)


def eligible_tones(log: MedicationLog) -> tuple[str, ...]:
    """這一頓可以用哪些語氣。

    家人版寫的是「家人就知道你吃過了」，只有逾時會通知「別人」的提醒才成立。
    長輩自己設的提醒（或自己掃藥袋建立的），逾時通報的對象就是他本人。
    """
    if log.alert_notify_user_id and log.alert_notify_user_id != log.user_id:
        return TONES
    return (TONE_CONTROL, TONE_BRIEF)


def outcome_of(log: MedicationLog) -> Optional[bool]:
    """這一頓提醒有沒有奏效：T+30 家屬通報之前按了「已服用」才算。

    通報之後才按（`missed` 之後轉成 `taken`）算失敗——提醒沒有在時限內奏效，
    家屬已經被打擾。還沒走完（`pending`）或規則被關掉（`cancelled`）的沒有結果。
    """
    if log.status == "missed":
        return False
    if log.status == "taken" and log.taken_at is not None:
        return ensure_aware_utc(log.taken_at) <= ensure_aware_utc(log.timeout_at)
    return None


def arm_counts(
    logs: Iterable[MedicationLog],
    arm_of: Callable[[MedicationLog], Optional[Hashable]],
    *,
    user_id: str,
) -> tuple[ArmCounts, ArmCounts]:
    """把有結果的紀錄依選項計數，回傳 `(其他長輩, 這位長輩)`。

    全體起點刻意排除這位長輩自己的紀錄：他的紀錄會以個人證據再疊一次，
    算進起點等於同一筆資料用了兩次。
    """
    others: ArmCounts = {}
    mine: ArmCounts = {}
    for log in logs:
        arm = arm_of(log)
        outcome = outcome_of(log)
        if arm is None or outcome is None:
            continue
        bucket = mine if log.user_id == user_id else others
        successes, failures = bucket.get(arm, (0, 0))
        bucket[arm] = (successes + 1, failures) if outcome else (successes, failures + 1)
    return others, mine


def choose_arm(
    arms: Sequence[Hashable],
    others: Mapping[Hashable, tuple[int, int]],
    mine: Mapping[Hashable, tuple[int, int]],
    *,
    sample: Callable[[float, float], float],
    prior_weight: float = PRIOR_WEIGHT,
) -> Hashable:
    """階層式 Thompson sampling：從 `arms` 裡挑一個。

    起點是其他長輩的成功率（加一平滑，沒有資料時為 0.5），換算成
    `prior_weight` 次提醒的份量；再加上這位長輩自己的成功、失敗次數。
    `sample(alpha, beta)` 注入是為了測試能用期望值代替亂數。
    """
    best, best_draw = arms[0], -1.0
    for arm in arms:
        other_successes, other_failures = others.get(arm, (0, 0))
        population_rate = (other_successes + 1) / (other_successes + other_failures + 2)
        my_successes, my_failures = mine.get(arm, (0, 0))
        draw = sample(
            prior_weight * population_rate + my_successes,
            prior_weight * (1 - population_rate) + my_failures,
        )
        if draw > best_draw:
            best, best_draw = arm, draw
    return best


def choose_variant(
    log: MedicationLog,
    history: Sequence[MedicationLog],
    *,
    sample: Callable[[float, float], float],
) -> ReminderVariant:
    """替這一頓挑語氣與催促時機：兩台拉霸各看自己的欄位、各自計數。

    `history` 是 `MedicationLogRepository.list_variant_outcomes()` 讀出來的
    紀錄。時機那台只看 `nudge_minutes` 還在的紀錄——長輩當天改了提醒設定時，
    那一頓的時機已經被清掉（見 `resync_pending_by_reminder`）。
    """
    tone = choose_arm(
        eligible_tones(log),
        *arm_counts(history, lambda past: past.reminder_tone, user_id=log.user_id),
        sample=sample,
    )
    nudge_minutes = choose_arm(
        NUDGE_MINUTES,
        *arm_counts(history, lambda past: past.nudge_minutes, user_id=log.user_id),
        sample=sample,
    )
    return ReminderVariant(tone=tone, nudge_minutes=nudge_minutes)
