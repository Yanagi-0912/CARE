"""查服藥狀況：在 LINE 裡問「我今天要吃什麼藥」「媽媽昨天有沒有吃藥」。

回覆原樣送給使用者、不經模型改寫（見 agent.py 的 medication_direct）：藥名、
時間、有沒有確認全部由這裡從資料庫組出來，模型沒有機會寫錯。

只輸出 GENERAL 級的資料——藥名、時段、飯前飯後、有沒有按下確認。適應症與調劑
機構是 SENSITIVE，這裡一律不碰。

用詞只講資料證明得了的事：系統只知道有沒有按下【已服用】，所以是「已確認服用」
「逾時未確認」，不是「吃了」「漏吃」。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal, Optional, Sequence

from fastapi import HTTPException

from app.i18n.messages import t
from app.models.family_tree import FamilyMember
from app.models.medication import (
    TAIPEI_TZ,
    MedicationLog,
    MedicationReminder,
    ensure_aware_utc,
    to_taipei_hm,
)
from app.services.family.person_resolution import PersonResolution, resolve_person

logger = logging.getLogger(__name__)

# 查詢範圍：今天加前 6 天，共 7 天。「這禮拜」「這幾天」是產品選定的範圍（今天
# 加最近幾天）裡最長的說法，再往前就是刻意沒做的「任意日期」。紀錄本身沒有保存
# 期限，所以這是範圍的決定，不是資料的限制。
MAX_DAYS = 7

CONFIRMED_MARK = "✓"
UNCONFIRMED_MARK = "•"

# ── 組資料 ──────────────────────────────────────────────────────────

SlotState = Literal["taken", "pending", "missed", "upcoming"]


@dataclass
class _Line:
    name: str
    confirmed: bool
    meal_timing: str = "none"
    time: str = ""


@dataclass
class _Slot:
    slot_type: str
    time: str
    state: SlotState
    taken_at: Optional[datetime] = None
    lines: list[_Line] = field(default_factory=list)


class MedicationStatusService:
    """組出查服藥狀況的回覆文字。

    repository 皆為 staticmethod 群組，沿用 dependencies.py 的慣例直接傳類別。
    """

    def __init__(
        self,
        *,
        family_tree_repository,
        authorization_service,
        reminder_repository,
        medication_repository,
        log_repository,
    ) -> None:
        self._trees = family_tree_repository
        self._authz = authorization_service
        self._reminders = reminder_repository
        self._medications = medication_repository
        self._logs = log_repository

    async def describe(
        self,
        asker_id: str,
        *,
        person: str = "",
        relationship: str = "",
        days_ago: int = 0,
        last_n_days: int = 0,
        now: Optional[datetime] = None,
        language: Optional[str] = None,
    ) -> str:
        """回答「誰、哪幾天」的服藥狀況。`language` 為 None 時用這次請求的語言。

        任何查詢失敗都回固定的錯誤訊息，不往外拋：這段文字會直接送給使用者，
        丟例外只會讓模型接手、自己編一個答案。
        """
        now = (now or datetime.now(TAIPEI_TZ)).astimezone(TAIPEI_TZ)
        days_ago = max(days_ago or 0, 0)
        last_n_days = max(last_n_days or 0, 0)
        if last_n_days < 2 and days_ago >= MAX_DAYS:
            return t("medstatus.out_of_range", language).format(n=MAX_DAYS)
        try:
            return await self._describe(
                asker_id, person, relationship, days_ago, last_n_days, now, language
            )
        except Exception:
            logger.exception("[MedicationStatus] 查詢失敗 asker=%s", asker_id)
            return t("medstatus.error", language)

    async def _describe(
        self,
        asker_id: str,
        person: str,
        relationship: str,
        days_ago: int,
        last_n_days: int,
        now: datetime,
        language: Optional[str],
    ) -> str:
        target_id, target_name = asker_id, None
        if resolve_person((), person=person, relationship=relationship).kind != "self":
            tree = await self._trees.get_by_user_id(asker_id)
            members = [
                m for m in (tree.family_members if tree else []) if m.user_id != asker_id
            ]
            if not members:
                return t("medstatus.no_family", language)
            resolution = resolve_person(members, person=person, relationship=relationship)
            if resolution.kind == "conflict":
                return t("medstatus.conflict", language).format(
                    query=(person or relationship).strip()
                )
            if resolution.kind == "ambiguous":
                return t("medstatus.ambiguous", language).format(
                    names=_names(resolution.candidates, language)
                )
            if resolution.kind == "not_found":
                return t("medstatus.not_found", language).format(
                    query=(person or relationship).strip(), names=_names(members, language)
                )
            target_id = resolution.member.user_id
            target_name = _display_name(resolution.member, language)
            try:
                # 「吃了沒」與藥名、服藥時段同屬 GENERAL：回答的都是「這一餐的藥
                # 處理了沒」，不揭露為什麼吃。這條路徑導入 RBAC 前不存在，照
                # has_legacy_equivalent=False 一律以矩陣判定，不受影子模式放寬。
                await self._authz.authorize(
                    asker_id, target_id, "GENERAL", "READ", has_legacy_equivalent=False
                )
            except HTTPException:
                return t("medstatus.no_permission", language).format(name=target_name)

        reminders = await self._reminders.list_reminders_by_user(target_id)
        if not reminders:
            if target_name is None:
                return t("medstatus.no_reminders.self", language)
            return t("medstatus.no_reminders.other", language).format(name=target_name)

        today = now.date()
        if last_n_days >= 2:
            text = await self._recent_days(
                target_id, target_name, today, min(last_n_days, MAX_DAYS), language
            )
            if last_n_days > MAX_DAYS:
                text = t("medstatus.clamped", language).format(n=MAX_DAYS) + "\n\n" + text
            return text
        if days_ago == 0:
            return await self._today(target_id, target_name, reminders, now, language)
        return await self._past_day(
            target_id, target_name, today - timedelta(days=days_ago), days_ago, language
        )

    async def _today(
        self,
        target_id: str,
        target_name: Optional[str],
        reminders: list[MedicationReminder],
        now: datetime,
        language: Optional[str],
    ) -> str:
        """已到時間的時段看服藥紀錄；還沒到的從規則推算（排程器要到點才建紀錄）。"""
        today = now.date()
        date_str = today.isoformat()
        logs = await self._logs.list_logs_by_user_between(target_id, *_day_bounds(today, today))
        active = await self._medications.find_active_by_ids(
            _unique(mid for r in reminders for mid in r.medication_ids), date_str
        )
        active_by_id = {m.id: m for m in active}
        reminder_by_id = {r.id: r for r in reminders}

        slots: list[_Slot] = []
        for log in logs:
            reminder = reminder_by_id.get(log.reminder_id)
            confirmed = (
                set(active_by_id) if log.status == "taken" else set(log.taken_medication_ids)
            )
            slots.append(
                _Slot(
                    slot_type=log.slot_type,
                    time=to_taipei_hm(log.scheduled_at),
                    state=log.status if log.status in ("taken", "missed") else "pending",
                    taken_at=log.taken_at,
                    lines=_expected_lines(reminder, active_by_id, confirmed),
                )
            )

        logged = {log.reminder_id for log in logs}
        now_hm = now.strftime("%H:%M")
        for reminder in reminders:
            # 時間已過卻沒有紀錄的規則不列：排程器不為「提醒建立之前」的時段補建
            # 紀錄，這裡也不能自己編一個狀態出來。
            if reminder.id in logged or reminder.scheduled_time <= now_hm:
                continue
            if not _runs_on(reminder, date_str):
                continue
            lines = _expected_lines(reminder, active_by_id, set())
            # 沒有有效藥品的時段不推播（見 MedicationScheduler），這裡也不列。
            if lines:
                slots.append(
                    _Slot(reminder.slot_type, reminder.scheduled_time, "upcoming", lines=lines)
                )

        slots.sort(key=lambda slot: slot.time)
        header = _day_header(target_name, today, 0, language)
        if not slots:
            return f"{header}\n\n{t('medstatus.no_slots', language)}"
        return header + "\n\n" + "\n\n".join(_render_slot(s, language) for s in slots)

    async def _past_day(
        self,
        target_id: str,
        target_name: Optional[str],
        day: date,
        days_ago: int,
        language: Optional[str],
    ) -> str:
        """過去只看服藥紀錄，不看規則。

        已確認的時段列出當時按下的藥名——`taken_medication_ids` 是按下當下存的，
        是事實。沒確認的時段不列藥名：那天該吃哪些只能用現在的規則回推，規則
        之後改過就會列錯。
        """
        logs = await self._logs.list_logs_by_user_between(target_id, *_day_bounds(day, day))
        header = _day_header(target_name, day, days_ago, language)
        if not logs:
            return f"{header}\n\n{t('medstatus.no_record', language)}"

        taken = await self._medications.find_by_ids(
            _unique(mid for log in logs for mid in log.taken_medication_ids)
        )
        name_by_id = {m.id: m.name for m in taken}
        slots = [
            _Slot(
                slot_type=log.slot_type,
                time=to_taipei_hm(log.scheduled_at),
                # 過了那天還是 pending，代表確認時限早就過了。
                state="taken" if log.status == "taken" else "missed",
                taken_at=log.taken_at,
                lines=[
                    _Line(name_by_id[mid], confirmed=True)
                    for mid in log.taken_medication_ids
                    if mid in name_by_id
                ],
            )
            for log in logs
        ]
        return header + "\n\n" + "\n\n".join(_render_slot(s, language) for s in slots)

    async def _recent_days(
        self,
        target_id: str,
        target_name: Optional[str],
        today: date,
        days: int,
        language: Optional[str],
    ) -> str:
        """每天一行，新的在上面；只點出沒確認的時段，不逐顆列藥。"""
        oldest = today - timedelta(days=days - 1)
        logs = await self._logs.list_logs_by_user_between(target_id, *_day_bounds(oldest, today))
        by_day: dict[date, list[MedicationLog]] = {}
        for log in logs:
            by_day.setdefault(_taipei_date(log.scheduled_at), []).append(log)

        lines = [
            _summary_line(
                today - timedelta(days=offset),
                by_day.get(today - timedelta(days=offset), []),
                is_today=offset == 0,
                language=language,
            )
            for offset in range(days)
        ]
        if target_name is None:
            header = t("medstatus.header.range_self", language).format(n=days)
        else:
            header = t("medstatus.header.range_other", language).format(
                name=target_name, n=days
            )
        return header + "\n\n" + "\n".join(lines)


# ── 小工具 ──────────────────────────────────────────────────────────


def _unique(ids) -> list[str]:
    seen: list[str] = []
    for item in ids:
        if item not in seen:
            seen.append(item)
    return seen


def _runs_on(reminder: MedicationReminder, date_str: str) -> bool:
    """與排程器挑規則的條件一致：啟用中，且當天落在 start_date～end_date 之間。"""
    return (
        reminder.enabled
        and (not reminder.start_date or reminder.start_date <= date_str)
        and (reminder.end_date is None or reminder.end_date >= date_str)
    )


def _expected_lines(
    reminder: Optional[MedicationReminder], active_by_id: dict, confirmed: set[str]
) -> list[_Line]:
    """規則當天仍有效的藥，依條目（飯前→飯後→其他）排列，與推播卡片同一個順序。"""
    if reminder is None:
        return []
    return [
        _Line(
            name=active_by_id[mid].name,
            confirmed=mid in confirmed,
            meal_timing=entry.meal_timing,
            time=entry.scheduled_time,
        )
        for entry in reminder.entries
        for mid in entry.medication_ids
        if mid in active_by_id
    ]


def _day_bounds(first: date, last: date) -> tuple[datetime, datetime]:
    """台北日期區間 [first, last] 對應的時間範圍，右端不含。"""
    start = datetime.combine(first, time.min, tzinfo=TAIPEI_TZ)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=TAIPEI_TZ)
    return start, end


def _taipei_date(moment: datetime) -> date:
    return ensure_aware_utc(moment).astimezone(TAIPEI_TZ).date()


def _display_name(member: FamilyMember, language: Optional[str]) -> str:
    return member.display_name or t("medstatus.unnamed", language)


def _names(members: Sequence[FamilyMember], language: Optional[str]) -> str:
    return t("medstatus.list_sep", language).join(_display_name(m, language) for m in members)


def _date_text(day: date, language: Optional[str]) -> str:
    return t("medstatus.date", language).format(m=day.month, d=day.day)


def _day_header(
    target_name: Optional[str], day: date, days_ago: int, language: Optional[str]
) -> str:
    label_key = {0: "medstatus.day.today", 1: "medstatus.day.yesterday"}.get(
        days_ago, "medstatus.day.other"
    )
    label = t(label_key, language).format(date=_date_text(day, language))
    if target_name is None:
        return t("medstatus.header.day_self", language).format(day=label)
    return t("medstatus.header.day_other", language).format(name=target_name, day=label)


def _slot_label(slot_type: str, time_text: str, language: Optional[str]) -> str:
    return f"{t(f'slot.{slot_type}', language)} {time_text}"


def _render_slot(slot: _Slot, language: Optional[str]) -> str:
    if slot.state == "taken" and slot.taken_at is not None:
        state = t("medstatus.state.taken_at", language).format(time=to_taipei_hm(slot.taken_at))
    else:
        state = t(f"medstatus.state.{slot.state}", language)
    head = t("medstatus.slot_line", language).format(
        slot=t(f"slot.{slot.slot_type}", language), time=slot.time, state=state
    )
    return "\n".join([head, *(_render_line(line, language) for line in slot.lines)])


def _render_line(line: _Line, language: Optional[str]) -> str:
    mark = CONFIRMED_MARK if line.confirmed else UNCONFIRMED_MARK
    if line.meal_timing == "none" or not line.time:
        return f"{mark} {line.name}"
    text = t("medstatus.med_with_timing", language).format(
        name=line.name, meal=t(f"meal.{line.meal_timing}", language), time=line.time
    )
    return f"{mark} {text}"


def _summary_line(
    day: date, logs: list[MedicationLog], *, is_today: bool, language: Optional[str]
) -> str:
    """一天一行：確認了幾個時段，再點出逾時未確認的、以及今天還在時限內的。

    今天還在確認時限內（pending）的時段另外標「還沒確認」，不跟逾時未確認混在
    一起——那不是結果，只是還沒到期。過去日子的 pending 代表時限早就過了。
    """
    date_text = _date_text(day, language)
    if not logs:
        return t("medstatus.summary.none", language).format(date=date_text)

    sep = t("medstatus.list_sep", language)
    missed = [log for log in logs if log.status != "taken" and not (is_today and log.status == "pending")]
    pending = [log for log in logs if is_today and log.status == "pending"]
    parts = [
        t("medstatus.summary.counts", language).format(
            date=date_text,
            taken=sum(1 for log in logs if log.status == "taken"),
            total=len(logs),
        )
    ]
    for state, group in (("missed", missed), ("pending", pending)):
        if group:
            parts.append(
                t("medstatus.summary.slots", language).format(
                    state=t(f"medstatus.state.{state}", language),
                    slots=sep.join(
                        _slot_label(log.slot_type, to_taipei_hm(log.scheduled_at), language)
                        for log in group
                    ),
                )
            )
    return t("medstatus.summary.joiner", language).join(parts)
