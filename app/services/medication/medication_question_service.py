"""拿使用者自己的藥單回答問題：「我這樣吃可以嗎」「這幾種藥能一起吃嗎」。

## 為什麼要有這個工具

在這之前，「我現在吃的藥」這類問題只有兩條路，兩條都答不了：

- `get_medication_status` 查得到藥單，但它只會把清單原樣送出，不回答任何問題。
  2026-09-23 線上就是這樣：使用者問「我 11 點喝牛奶、12 點吃藥、等等吃午餐，
  這樣可以嗎」，收到的是一張今天的用藥清單。
- `get_rag_answer` 答得了藥理，但它看不到這個人在吃什麼——個人健康檔案從來
  沒有進過 RAG 的 query（只在 agent 自己寫回覆時才看得到）。

所以這裡把兩邊接起來：藥名從資料庫拿，藥理交給既有的 RAG 管線，兩者之間唯一
的橋是「把藥名附在問題後面」。

## 哪些話是程式講的、哪些是模型講的

**時間的事一律程式算**：這一頓比排定時間晚了多久、距離下一頓還剩多久、排定
的間隔本來是多久。這三件事是資料庫裡的減法，模型沒有比程式準的餘地，而它恰好
是使用者最需要、RAG 最答不出來的部分——沒有任何一篇衛教文章知道他今天拖到
幾點才吃。

**藥理交給 RAG**，照既有管線（知識庫→CRAG→網搜），不另外開一條只靠模型自己
知識回答的路。

## 不講判斷，只講數字

時間那段只陳述「實際間隔 6 小時、排定間隔 10 小時」，不說「太近了」「要延後」
——隔多久算安全是醫囑，不是我們算得出來的。差距小於一小時就不提：提醒本身的
解析度就是整點時段，差不到一小時講出來只是雜訊。

## 只輸出 GENERAL 級的資料

藥名、時段、飯前飯後。適應症（`indication`、`spc_indication`）與調劑機構是
SENSITIVE，這裡一律不碰——同 `MedicationStatusService`。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Optional

from app.core.medication_facts import set_request_medication_facts
from app.i18n.messages import insert_before_sources, t
from app.models.medication import (
    TAIPEI_TZ,
    MedicationLog,
    MedicationReminder,
    ensure_aware_utc,
    to_taipei_hm,
)
from app.services.medication.target_resolution import resolve_medication_target
from app.services.rag.fail_messages import is_rag_fail

logger = logging.getLogger(__name__)

# 附在 RAG query 後面的藥名上限。查詢是拿去做向量檢索與精排的，把十幾個藥名
# 全串上去會稀釋掉問題本身；8 個已經涵蓋實際藥袋的規模（掃描實測最多 7 種）。
MAX_QUERY_DRUGS = 8

# 事實一句一行。這段是拿來對時間的，擠成一整段會讓「只隔 6 小時」那一句被淹掉
# （同用藥提醒改成逐藥卡片的理由）。不是 i18n 訊息：換行在每種語言都一樣。
FACT_SEPARATOR = "\n"

# 時間差距要到多少才值得講。見模組說明「不講判斷，只講數字」。
MIN_NOTABLE_GAP = timedelta(hours=1)


class MedicationQuestionService:
    def __init__(
        self,
        *,
        family_tree_repository,
        authorization_service,
        reminder_repository,
        medication_repository,
        log_repository,
        rag_answer_service,
    ) -> None:
        self._trees = family_tree_repository
        self._authz = authorization_service
        self._reminders = reminder_repository
        self._medications = medication_repository
        self._logs = log_repository
        self._rag = rag_answer_service

    async def answer(
        self,
        asker_id: str,
        question: str,
        *,
        person: str = "",
        relationship: str = "",
        now: Optional[datetime] = None,
        language: Optional[str] = None,
    ) -> str:
        """回答與這個人目前用藥有關的問題。

        任何失敗都回固定文字、不往外拋：回傳值會直接送給使用者（見 agent.py 的
        `medication_question_direct`），丟例外只會讓模型接手自己編一個答案。
        """
        now = (now or datetime.now(TAIPEI_TZ)).astimezone(TAIPEI_TZ)
        try:
            return await self._answer(asker_id, question, person, relationship, now, language)
        except Exception:
            logger.exception("[MedicationQuestion] 失敗 asker=%s", asker_id)
            return t("medstatus.error", language)

    async def _answer(
        self,
        asker_id: str,
        question: str,
        person: str,
        relationship: str,
        now: datetime,
        language: Optional[str],
    ) -> str:
        target = await resolve_medication_target(
            asker_id=asker_id,
            person=person,
            relationship=relationship,
            trees=self._trees,
            authz=self._authz,
            language=language,
        )
        if not target.ok:
            return target.error

        reminders = await self._reminders.list_reminders_by_user(target.user_id)
        today = now.date()
        date_str = today.isoformat()
        active = await self._medications.find_active_by_ids(
            _unique(mid for r in reminders for mid in r.medication_ids), date_str
        )
        if not active:
            # 沒有登記的藥就沒有「你的藥單」可用，這個工具不成立。照實說，
            # 而不是偷偷改用一般衛教回答——使用者問的是「我的藥」。
            if target.display_name is None:
                return t("medstatus.no_reminders.self", language)
            return t("medstatus.no_reminders.other", language).format(
                name=target.display_name
            )

        logs = await self._logs.list_logs_by_user_between(
            target.user_id, *_day_bounds(today)
        )
        facts = self._facts(
            target.display_name, active, reminders, logs, date_str, language
        )

        rag_answer = await self._rag.answer(_query(question, active))
        if is_rag_fail(rag_answer):
            # RAG 答不出來不代表整則回覆沒有價值：時間那段是程式算的，跟 RAG
            # 成不成功無關，而它正是使用者最需要的部分。所以照送，只把藥理那
            # 半段換成「請問藥師」。
            logger.info("[MedicationQuestion] RAG 無答案，只回登記資料")
            return f"{t('medq.no_answer', language)}\n\n{facts}"

        prefix = t("agent.rag_prefix", language)
        if prefix and not rag_answer.lstrip().startswith(prefix):
            rag_answer = f"{prefix}\n{rag_answer}"
        return insert_before_sources(rag_answer, facts)

    def _facts(
        self,
        target_name: Optional[str],
        active,
        reminders: list[MedicationReminder],
        logs: list[MedicationLog],
        date_str: str,
        language: Optional[str],
    ) -> str:
        """這個人登記了什麼、今天的時段怎麼跑——全部是資料庫裡的事實。"""
        parts = [
            t("medq.meds", language).format(
                n=len(active),
                names=t("medstatus.list_sep", language).join(m.name for m in active),
            )
        ]

        slot_times = _slot_times_today(reminders, logs, date_str)
        if slot_times:
            parts.append(
                t("medq.slots_today", language).format(
                    slots=t("medstatus.list_sep", language).join(
                        _slot_label(slot, hhmm, language) for slot, hhmm in slot_times
                    )
                )
            )
        parts.extend(_timing_notes(logs, slot_times, language))
        parts.append(t("medq.ask_pharmacist", language))

        body = FACT_SEPARATOR.join(parts)
        key = "medq.facts_self" if target_name is None else "medq.facts_other"
        block = t(key, language).format(name=target_name or "", body=body)

        # 呈現層要把這幾行做成卡片上獨立的一塊，所以除了回傳文字，也把原始清單
        # 與組好的整段交給本輪的 holder（理由見 app/core/medication_facts.py）。
        # 文字那份不能省：純文字退路與語音朗讀都只拿得到它。
        set_request_medication_facts(parts, block, target_name)
        return block


# ── 小工具 ──────────────────────────────────────────────────────────


def _unique(ids) -> list[str]:
    seen: list[str] = []
    for item in ids:
        if item not in seen:
            seen.append(item)
    return seen


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=TAIPEI_TZ)
    return start, start + timedelta(days=1)


def _runs_on(reminder: MedicationReminder, date_str: str) -> bool:
    """與排程器挑規則的條件一致（同 `MedicationStatusService._runs_on`）。"""
    return (
        reminder.enabled
        and (not reminder.start_date or reminder.start_date <= date_str)
        and (reminder.end_date is None or reminder.end_date >= date_str)
    )


def _slot_label(slot_type: str, hhmm: str, language: Optional[str]) -> str:
    return f"{t(f'slot.{slot_type}', language)} {hhmm}"


def _slot_times_today(
    reminders: list[MedicationReminder], logs: list[MedicationLog], date_str: str
) -> list[tuple[str, str]]:
    """今天的所有時段 (slot_type, HH:MM)，依時間排序。

    已展開的紀錄與還沒到的規則都要算進來：距離下一頓還剩多久，下一頓多半是
    「還沒展開紀錄」的那個時段。
    """
    seen: dict[str, str] = {}
    for log in logs:
        seen[to_taipei_hm(log.scheduled_at)] = log.slot_type
    for reminder in reminders:
        if _runs_on(reminder, date_str):
            seen.setdefault(reminder.scheduled_time, reminder.slot_type)
    return [(seen[hhmm], hhmm) for hhmm in sorted(seen)]


def _timing_notes(
    logs: list[MedicationLog],
    slot_times: list[tuple[str, str]],
    language: Optional[str],
) -> list[str]:
    """今天最近一次已確認的服藥，比排定晚了多久、離下一頓還剩多久。

    只看最近一次：把今天每一頓的偏移都列出來，使用者要找的那一行會被淹掉，
    而他問的永遠是剛剛那一頓。
    """
    taken = [
        log
        for log in logs
        if log.status == "taken" and log.taken_at is not None
    ]
    if not taken:
        return []
    log = max(taken, key=lambda item: ensure_aware_utc(item.taken_at))
    scheduled = ensure_aware_utc(log.scheduled_at).astimezone(TAIPEI_TZ)
    actual = ensure_aware_utc(log.taken_at).astimezone(TAIPEI_TZ)

    notes: list[str] = []
    delay = actual - scheduled
    if delay >= MIN_NOTABLE_GAP:
        notes.append(
            t("medq.late", language).format(
                slot=_slot_label(log.slot_type, to_taipei_hm(log.scheduled_at), language),
                actual=actual.strftime("%H:%M"),
                delay=_duration(delay, language),
            )
        )

    nxt = _next_slot_after(slot_times, scheduled)
    if nxt is None:
        return notes
    next_slot, next_at = nxt
    planned_gap = next_at - scheduled
    actual_gap = next_at - actual
    if planned_gap - actual_gap >= MIN_NOTABLE_GAP and actual_gap > timedelta(0):
        notes.append(
            t("medq.gap", language).format(
                actual=actual.strftime("%H:%M"),
                next_slot=_slot_label(next_slot, next_at.strftime("%H:%M"), language),
                actual_gap=_duration(actual_gap, language),
                planned_gap=_duration(planned_gap, language),
            )
        )
    return notes


def _next_slot_after(
    slot_times: list[tuple[str, str]], scheduled: datetime
) -> Optional[tuple[str, datetime]]:
    """排在 `scheduled` 之後的下一個時段。今天沒有下一頓時回 None。"""
    for slot_type, hhmm in slot_times:
        hour, minute = map(int, hhmm.split(":"))
        moment = scheduled.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if moment > scheduled:
            return slot_type, moment
    return None


def _duration(delta: timedelta, language: Optional[str]) -> str:
    """「4 小時」「6 小時 30 分」。不足一小時的差距不會走到這裡（見 MIN_NOTABLE_GAP）。"""
    minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    if minutes:
        return t("medq.duration_hm", language).format(h=hours, m=minutes)
    return t("medq.duration_h", language).format(h=hours)


def _query(question: str, active) -> str:
    """交給 RAG 的字串：問題原文，後面接藥名。

    藥名附在**後面**而不是重寫問題：這個字串同時是檢索 query 與生成用的問題，
    改寫問題會讓模型回答一個使用者沒問的東西。學名優先於商品名——知識庫與可
    信網域寫的是成分（`Esomeprazole`），不是藥袋上的「耐適恩錠」。
    """
    names: list[str] = []
    for med in active:
        name = (med.generic_name or "").strip() or med.name.strip()
        if name and name not in names:
            names.append(name)
    if not names:
        return question.strip()
    listed = "、".join(names[:MAX_QUERY_DRUGS])
    return f"{question.strip()}（目前正在服用：{listed}）"
