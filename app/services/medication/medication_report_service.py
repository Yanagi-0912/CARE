"""在聊天裡回報吃過藥了：「我 12 點吃了藥」「早上那頓吃了」。

## 為什麼需要

2026-09-23 線上的那一則：使用者在訊息裡明說「我剛剛 12 點吃了藥」，下一秒
CARE 回的清單上，早上那一頓還寫著「逾時未確認」。確認只認推播卡片上的按鈕，
講了不算——他人在聊天視窗裡，按鈕在幾十則訊息之前。

## 只有本人能確認

與按鈕同一條界線（`MedicationService.confirm_medication` 對 `log.user_id !=
user_id` 回 403）。家人替他按下「吃過了」是替另一個人的病歷作證，而聊天裡的
「我媽吃了」是轉述，不是當事人自己說的。所以這裡不接受 `person`，遇到就照實
說只能由本人回報。

## 挑哪一頓

使用者說的時間就是最強的訊號：`12:00` 指向「排定時間在 12:00 之前、還沒確認」
的那一頓裡最晚的一個。時段詞（「早上的」）拿來先過濾。剩下不只一個候選就反問
——猜錯會把另一頓標成吃過了，而那一頓之後就不會再催促，也不會通知家人。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Optional

from fastapi import HTTPException

from app.i18n.messages import t
from app.models.medication import (
    TAIPEI_TZ,
    MedicationLog,
    ensure_aware_utc,
    to_taipei_hm,
)
from app.services.family.person_resolution import resolve_person
from app.services.line_messaging.flex.medication_flex import get_slot_display_name
from app.services.line_messaging.flex.medication_report_flex import (
    SlotChoice,
    as_payload,
    build_report_bubble,
    build_slot_choice_bubble,
)
from resources.flex_messages.theme import resolve_theme

logger = logging.getLogger(__name__)

_HHMM = re.compile(r"^\s*([01]?\d|2[0-3])\s*[:：]\s*([0-5]\d)\s*$")

# 還沒確認、可以被回報的狀態。`cancelled`（時段被關掉）本來就不在
# `list_logs_by_user_between` 的結果裡，這裡也不特別找回來：那一頓使用者已經
# 自己關掉了，聊天裡再替他標成吃過沒有意義。
_OPEN_STATUSES = ("pending", "missed")

# 模型填進來的時段。不在這四個裡面（含空字串）就當成沒指定，改用時間挑。
_SLOT_TYPES = ("morning", "noon", "evening", "bedtime")


class MedicationReportService:
    def __init__(
        self,
        *,
        medication_service,
        log_repository,
    ) -> None:
        self._medications = medication_service
        self._logs = log_repository

    async def record_taken(
        self,
        asker_id: str,
        *,
        person: str = "",
        relationship: str = "",
        slot: str = "",
        taken_time: str = "",
        now: Optional[datetime] = None,
        language: Optional[str] = None,
    ) -> str:
        """把某一頓標成已確認服用，回傳要送給使用者的整段文字。

        任何失敗都回固定文字、不往外拋（理由同 `MedicationStatusService.describe`）。
        """
        now = (now or datetime.now(TAIPEI_TZ)).astimezone(TAIPEI_TZ)
        if resolve_person((), person=person, relationship=relationship).kind != "self":
            return t("medreport.self_only", language)
        try:
            return await self._record(asker_id, slot, taken_time, now, language)
        except HTTPException:
            # confirm_medication 的 403／404。使用者只要知道這次沒記上。
            logger.warning("[MedicationReport] 確認被拒 asker=%s", asker_id)
            return t("medreport.error", language)
        except Exception:
            logger.exception("[MedicationReport] 失敗 asker=%s", asker_id)
            return t("medreport.error", language)

    async def _record(
        self,
        asker_id: str,
        slot: str,
        taken_time: str,
        now: datetime,
        language: Optional[str],
    ) -> str:
        logs = await self._logs.list_logs_by_user_between(asker_id, *_day_bounds(now.date()))
        candidates = [log for log in logs if log.status in _OPEN_STATUSES]
        if slot in _SLOT_TYPES:
            narrowed = [log for log in candidates if log.slot_type == slot]
            if not narrowed:
                # 那個時段今天沒有待確認的紀錄：可能已經確認過，也可能今天
                # 根本沒排。兩者都不該改動別的時段——使用者指名了哪一頓。
                return t("medreport.slot_not_open", language).format(
                    slot=t(f"slot.{slot}", language)
                )
            candidates = narrowed
        if not candidates:
            return t("medreport.nothing_open", language)

        moment = _parse_time(taken_time, now)
        chosen = _pick(candidates, moment)
        if chosen is None:
            return self._ask_which_slot(candidates, moment, language)
        return await self.confirm_slot(asker_id, chosen.id, moment, language=language)

    async def confirm_slot(
        self,
        asker_id: str,
        log_id: str,
        moment: datetime,
        *,
        language: Optional[str] = None,
    ) -> str:
        """確認指定的那一筆並組出回覆。

        反問卡上的按鈕（postback）與聊天直接挑中的那條路共用這裡：兩邊確認完
        要顯示的是同一張卡，分開寫遲早會分岔。
        """
        updated = await self._medications.confirm_medication(
            log_id, asker_id, taken_at=moment
        )
        names = await self._medications.list_medication_names_for_log(updated)
        logger.info(
            "[MedicationReport] 已由聊天回報確認 log=%s slot=%s", updated.id, updated.slot_type
        )
        slot_name = get_slot_display_name(updated.slot_type, language)
        scheduled = to_taipei_hm(updated.scheduled_at)
        taken = to_taipei_hm(updated.taken_at, default=moment.strftime("%H:%M"))
        text = t("medreport.done", language).format(
            slot=f"{slot_name} {scheduled}", time=taken
        )
        if names:
            text += "\n" + "\n".join(f"✓ {name}" for name in names)

        bubble = build_report_bubble(
            log_id=updated.id,
            slot_type=updated.slot_type,
            scheduled_time=scheduled,
            taken_time=taken,
            medication_names=names,
            ft=resolve_theme(),
            language=language,
        )
        payload = as_payload(
            bubble,
            t("flex.medreport.alt.done", language).format(slot=f"{slot_name} {scheduled}"),
            speech_text=text,
        )
        # 卡片組不出來就退回純文字：這則回覆是使用者用來核對「記到對的那一頓」
        # 的依據，不能因為排版而整個消失。
        return json.dumps(payload, ensure_ascii=False) if payload else text

    async def undo(
        self,
        asker_id: str,
        log_id: str,
        *,
        language: Optional[str] = None,
    ) -> tuple[Optional[dict], str]:
        """撤銷一次聊天回報（卡片上的「記錯了」）。

        回 `(bubble, 文字)`：bubble 為 None 時呼叫端只送文字。撤銷不了（不是
        本人、已經被撤銷過、或狀態已經不是 taken）一律回固定文案，不丟例外
        ——第二次按下去看到錯誤訊息，使用者會以為第一次也沒生效。
        """
        try:
            reverted = await self._medications.revert_confirmation(log_id, asker_id)
        except Exception:
            logger.exception("[MedicationReport] 撤銷失敗 asker=%s", asker_id)
            return None, t("medreport.undo_failed", language)
        if reverted is None:
            return None, t("medreport.undo_failed", language)

        slot_name = get_slot_display_name(reverted.slot_type, language)
        scheduled = to_taipei_hm(reverted.scheduled_at)
        bubble = build_report_bubble(
            log_id=log_id,
            slot_type=reverted.slot_type,
            scheduled_time=scheduled,
            taken_time="",
            medication_names=(),
            ft=resolve_theme(),
            language=language,
            reverted=True,
        )
        text = t("flex.medreport.alt.reverted", language).format(
            slot=f"{slot_name} {scheduled}"
        )
        return bubble, text

    def _ask_which_slot(
        self, candidates: list[MedicationLog], moment: datetime, language: Optional[str]
    ) -> str:
        """反問是哪一頓。候選做成按鈕，使用者不必再打一次字、系統也不必再判讀一次。"""
        text = t("medreport.which_slot", language).format(
            slots=t("medstatus.list_sep", language).join(
                _slot_label(log, language) for log in candidates
            )
        )
        bubble = build_slot_choice_bubble(
            choices=[
                SlotChoice(log.id, log.slot_type, to_taipei_hm(log.scheduled_at))
                for log in candidates
            ],
            taken_time=moment.strftime("%H:%M"),
            ft=resolve_theme(),
            language=language,
        )
        payload = as_payload(bubble, t("flex.medreport.header.which", language), speech_text=text)
        return json.dumps(payload, ensure_ascii=False) if payload else text


# ── 小工具 ──────────────────────────────────────────────────────────


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=TAIPEI_TZ)
    return start, start + timedelta(days=1)


def _slot_label(log: MedicationLog, language: Optional[str]) -> str:
    return f"{t(f'slot.{log.slot_type}', language)} {to_taipei_hm(log.scheduled_at)}"


def _parse_time(taken_time: str, now: datetime) -> datetime:
    """使用者說的時刻換成今天的台北時間；沒說或看不懂就是現在。

    晚於現在的時刻一律當成現在：今天還沒到的時刻不可能已經吃過，那是模型把
    「等等要吃」聽成「吃了」，或是把排定時間填了進來。
    """
    match = _HHMM.match(taken_time or "")
    if not match:
        return now
    stated = now.replace(
        hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0
    )
    return now if stated > now else stated


def _pick(candidates: list[MedicationLog], moment: datetime) -> Optional[MedicationLog]:
    """挑出使用者說的那一頓，挑不準時回 None 讓呼叫端反問。"""
    due = [
        log
        for log in candidates
        if ensure_aware_utc(log.scheduled_at).astimezone(TAIPEI_TZ) <= moment
    ]
    if due:
        # 已經到時間、還沒確認的裡面最晚的那一頓。更早的那些是更久以前就沒
        # 確認的，使用者說「剛剛吃了」指的不會是它們。
        return max(due, key=lambda log: ensure_aware_utc(log.scheduled_at))
    # 都還沒到時間：只有一個候選時就是它（提早吃了），多個就分不出來。
    return candidates[0] if len(candidates) == 1 else None
