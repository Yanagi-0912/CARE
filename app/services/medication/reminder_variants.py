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

計數每一輪排程彙整一次（`build_variant_stats`），之後每一頓只查表，不再逐頓掃過
全部歷史。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Hashable, Iterable, Mapping, Optional, Sequence

from app.models.medication import URGENT_AFTER_ANCHOR_MINUTES, MedicationLog, ensure_aware_utc

TONE_CONTROL = "control"
TONE_FAMILY = "family"
TONE_BRIEF = "brief"
TONES: tuple[str, ...] = (TONE_CONTROL, TONE_FAMILY, TONE_BRIEF)

# 催促在最晚服藥時刻之後幾分鐘送。對照組是上線前的固定值。T+30 的家屬通報不動，
# 所以選項最晚只到 20：再晚，長輩收到催促後只剩不到 10 分鐘可以回應。
NUDGE_CONTROL = URGENT_AFTER_ANCHOR_MINUTES
NUDGE_MINUTES: tuple[int, ...] = (NUDGE_CONTROL, 15, 10)

# 其他長輩的成功率當起點時，份量相當於幾次提醒。一位長輩自己累積到這個次數
# （例如一天 3 次，約 3 天），他自己的反應就與全體同重；在那之前以全體為主，
# 前幾天才不會因為一兩次沒按就把某個選項打入冷宮。
PRIOR_WEIGHT = 10.0

Counts = tuple[int, int]
"""(成功次數, 失敗次數)"""


@dataclass(frozen=True)
class ReminderVariant:
    tone: str
    # None：這一頓不進時機拉霸（T+0 晚送），催促維持展開時的預設值。
    nudge_minutes: Optional[int]


def is_family_managed(log: MedicationLog) -> bool:
    """逾時會通知「別人」的提醒，也就是家屬替長輩設的。長輩自己設的提醒（或自己
    掃藥袋建立的），逾時通報的對象就是他本人。"""
    return bool(log.alert_notify_user_id) and log.alert_notify_user_id != log.user_id


def eligible_tones(log: MedicationLog) -> tuple[str, ...]:
    """這一頓可以用哪些語氣。家人版寫的是「家人就知道你吃過了」，只有家屬設的提醒
    才成立。"""
    return TONES if is_family_managed(log) else (TONE_CONTROL, TONE_BRIEF)


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


@dataclass(frozen=True)
class VariantStats:
    """一輪排程彙整一次的計數。

    語氣依提醒是誰設的分兩群計數（鍵的第一個 bool 是 `is_family_managed`）：家人版
    只會出現在家屬設的提醒上，兩群長輩原本的準時率若不同，混在一起算就會把這個差距
    當成語氣的效果。催促時機兩群都會被分配到三個選項，不必分開。
    """

    tone: Mapping[tuple[bool, str], Counts]
    tone_by_user: Mapping[tuple[str, bool, str], Counts]
    nudge: Mapping[int, Counts]
    nudge_by_user: Mapping[tuple[str, int], Counts]


def _add(table: dict, key: Hashable, success: bool) -> None:
    successes, failures = table.get(key, (0, 0))
    table[key] = (successes + 1, failures) if success else (successes, failures + 1)


def build_variant_stats(history: Iterable[MedicationLog]) -> VariantStats:
    """把 `list_variant_outcomes()` 讀出的紀錄彙整成計數表（掃一次）。

    時機被清掉的那一頓（長輩當天改了提醒設定）只算進語氣。
    """
    tone: dict = {}
    tone_by_user: dict = {}
    nudge: dict = {}
    nudge_by_user: dict = {}
    for log in history:
        success = outcome_of(log)
        if success is None:
            continue
        if log.reminder_tone is not None:
            group = is_family_managed(log)
            _add(tone, (group, log.reminder_tone), success)
            _add(tone_by_user, (log.user_id, group, log.reminder_tone), success)
        if log.nudge_minutes is not None:
            _add(nudge, log.nudge_minutes, success)
            _add(nudge_by_user, (log.user_id, log.nudge_minutes), success)
    return VariantStats(tone, tone_by_user, nudge, nudge_by_user)


def choose_arm(
    arms: Sequence[Hashable],
    others: Mapping[Hashable, Counts],
    mine: Mapping[Hashable, Counts],
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


def _split(
    arms: Sequence[Hashable],
    total_of: Callable[[Hashable], Counts],
    mine_of: Callable[[Hashable], Counts],
) -> tuple[dict, dict]:
    """(其他長輩, 這位長輩)。全體起點扣掉這位長輩自己的紀錄：他的紀錄會以個人
    證據再疊一次，算進起點等於同一筆資料用了兩次。"""
    mine = {arm: mine_of(arm) for arm in arms}
    others = {
        arm: (total_of(arm)[0] - mine[arm][0], total_of(arm)[1] - mine[arm][1]) for arm in arms
    }
    return others, mine


def choose_variant(
    log: MedicationLog,
    stats: VariantStats,
    *,
    sample: Callable[[float, float], float],
    apply_timing: bool = True,
) -> ReminderVariant:
    """替這一頓挑語氣與催促時機：兩台拉霸各看自己的計數。

    `apply_timing=False`（T+0 晚送，見排程器的 `_ON_TIME_T0`）時只挑語氣。
    """
    group = is_family_managed(log)
    tones = eligible_tones(log)
    tone = choose_arm(
        tones,
        *_split(
            tones,
            lambda arm: stats.tone.get((group, arm), (0, 0)),
            lambda arm: stats.tone_by_user.get((log.user_id, group, arm), (0, 0)),
        ),
        sample=sample,
    )
    if not apply_timing:
        return ReminderVariant(tone=tone, nudge_minutes=None)
    nudge_minutes = choose_arm(
        NUDGE_MINUTES,
        *_split(
            NUDGE_MINUTES,
            lambda arm: stats.nudge.get(arm, (0, 0)),
            lambda arm: stats.nudge_by_user.get((log.user_id, arm), (0, 0)),
        ),
        sample=sample,
    )
    return ReminderVariant(tone=tone, nudge_minutes=nudge_minutes)
