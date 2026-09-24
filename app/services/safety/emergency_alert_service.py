"""
對話中判定為緊急時，通報合格家屬。

沿用 otc_alert_service 的形狀（收件人判定 → 逐人取偏好 → 推 Flex，任何失敗都
吞在入口不影響主流程），差別在觸發來源：那邊是「新增了一盒成藥」這種可以慢慢
處理的事件，這邊是使用者此刻正在說的話。
為什麼每位收件人各自套用自己的 notify_family：
    這個開關的語意是「我要不要收到家人的健康通知」，屬於收件人而非當事人
    （用藥提醒已是如此）。危機通報刻意**不豁免**它：使用者把開關關掉是明示的
    意願，緊急時無視它等於系統自行決定「我比你更知道什麼對你好」。代價是
    關掉的人不會收到——那正是他選的。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from app.core.user_font_size import DEFAULT_USER_FONT_SIZE, normalize_user_font_size
from app.core.user_language import DEFAULT_USER_LANGUAGE, normalize_user_language
from app.i18n.messages import t
from app.services.family.person_resolution import PersonResolution
from app.services.medical.symptom_classification.urgency import AffectedPerson
from resources.flex_messages.medical_messages.emergency_family_alert_flex_message import (
    alt_text,
    build_emergency_family_flex,
)
from resources.flex_messages.size_guard import fits

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Services:EmergencyAlert]"

NOTIFICATION_KIND = "emergency_detected"


class _Replier(Protocol):
    # push_flex 收 SDK 的 FlexMessage，且**把所有例外吞掉只回傳 False**。
    # 因此判斷「有沒有真的送出去」必須看回傳值，不能只靠 try/except——初版
    # 只接例外，於是推播失敗時 sent 仍為 True，當事人收到了「我已經讓你的
    # 家人知道」而其實沒有人收到。
    async def push_flex(self, user_id: str, flex: Any) -> bool: ...
    async def push_text(self, user_id: str, text: str) -> bool: ...


class EmergencyFamilyAlertService:
    def __init__(
        self,
        *,
        replier: _Replier,
        authorization_service: Any = None,
        user_profile_service: Any = None,
    ) -> None:
        self._replier = replier
        self._authorization_service = authorization_service
        self._user_profile_service = user_profile_service

    async def patients_to_notify(
        self, reporter_id: str, resolved: tuple[ResolvedAffected, ...]
    ) -> list[str]:
        """這次回報該通知誰的家人：只有能唯一確定、且連結經伺服器驗證的病人。

        - 本人急症：病人就是回報者。
        - 家人急症：必須在回報者的名單中唯一解析，**而且**回報者也在病人自己的
          族譜裡（`resolve_role` 不為 None，任何角色皆可回報）。只在我的名單裡
          單方面列了對方不算——那是自助式連結。
        - 同稱謂多人、名單查不到、朋友、路人：不通知任何家庭。猜錯就是去驚動
          另一個家庭。認不出是誰的人在 `resolve_affected` 已當成本人。
        - 本身沒有急症的人（同句的輕微不適）不通知。

        這一步只確認連結，不做 SENSITIVE READ 授權、不讀病人 profile：回報緊急
        事件不等於取得病人健康資料的讀取權。
        """
        patients: list[str] = []
        for item in resolved:
            if not item.person.urgent:
                continue
            if item.person.kind == "self":
                candidate = reporter_id
            elif item.is_resolved_member and item.resolution.member is not None:
                candidate = item.resolution.member.user_id
                if not await self._linked(reporter_id, candidate):
                    continue
            else:
                continue
            if candidate and candidate not in patients:
                patients.append(candidate)
        return patients

    async def _linked(self, reporter_id: str, patient_user_id: str) -> bool:
        if self._authorization_service is None or not reporter_id:
            return False
        try:
            role = await self._authorization_service.resolve_role(
                reporter_id, patient_user_id
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 家庭連結驗證失敗，本次不通報該病人：%s",
                type(exc).__name__,
            )
            return False
        return role is not None

    async def notify(
        self,
        patient_user_id: str,
        reason: str,
        words: str = "",
        *,
        reporter_id: str = "",
    ) -> bool:
        """對外的唯一入口。回傳是否真的送出給任何人；任何失敗都吞在這裡。

        `patient_user_id` 是**解析後的病人**，不是發話者：孫子回報阿公跌倒時
        通知的是阿公的照顧者（見 `patients_to_notify`）。
        `reason` 是急迫度判斷產生的白話說明（系統為什麼判定為緊急）；
        `words` 是發話者的原話，逐字轉發不改寫——「喝了 3 瓶農藥」與
        「可能需要協助」對家屬是完全不同的兩件事，而劑量正是急救要問的第一個
        問題（見卡片模組註解）。
        `reporter_id` 是發話者。與病人不同時卡片標成「{回報者} 回報」，原話
        標成回報者說的，不冒充病人發言；他也已經知道這件事，不在收件人之列。
        """
        if not patient_user_id:
            return False
        try:
            return await self._notify(
                patient_user_id, reason, words, reporter_id=reporter_id
            )
        except Exception:  # noqa: BLE001
            logger.error(
                f"{LOGGER_HEADER_TEXT} 家人通報失敗，當事人的回覆不受影響",
                exc_info=True,
            )
            return False

    async def _notify(
        self,
        patient_user_id: str,
        reason: str,
        words: str = "",
        *,
        reporter_id: str = "",
    ) -> bool:
        recipients = [
            uid for uid in await self._recipients(patient_user_id) if uid != reporter_id
        ]
        if not recipients:
            logger.info(f"{LOGGER_HEADER_TEXT} 沒有合格收件人，本次不通報")
            return False

        patient_name = await self._display_name(patient_user_id) or t(
            "emergency_family.fallback_name", DEFAULT_USER_LANGUAGE
        )
        # 代為回報：卡片揭示回報者。讀的是回報者自己的名字，不是病人的資料。
        reporter_name = (
            await self._display_name(reporter_id)
            if reporter_id and reporter_id != patient_user_id
            else None
        )
        sent = False
        for member_id in recipients:
            language, font_size, notify_family = await self._display_prefs(member_id)
            if not notify_family:
                # 抑制的只有推播。判定本身仍然發生過，當事人也已經收到紅卡。
                logger.info(
                    f"{LOGGER_HEADER_TEXT} 收件人關閉了家人通知，略過推播"
                )
                continue
            flex = build_emergency_family_flex(
                patient_name=patient_name,
                # 本地模型判定的緊急不帶白話說明，LLM 也可能回空字串；卡片上
                # 那一格不能空著，改用收件人語言的泛稱。
                reason=reason or t("emergency_family.default_reason", language),
                words=words,
                reporter_name=reporter_name,
                language=language,
                font_size=font_size,
            )
            if await self._push(member_id, flex, patient_name, language):
                sent = True

        logger.info(f"{LOGGER_HEADER_TEXT} 通報完成，sent=%s", sent)
        return sent

    async def _push(
        self, member_id: str, flex: Any, patient_name: str, language: str
    ) -> bool:
        """送出一則，回傳是否真的送到。

        寧可少了版面也不要讓家人什麼都收不到——這是本專案唯一一種「沒收到就
        可能來不及」的通知，沿用 otc_alert_service 對大小上限的同樣處置。

        兩種失敗都要退回純文字：超過大小上限（LINE 會整則退回），以及
        push_flex 回傳 False（它吞掉例外，不會拋）。
        """
        text_fallback = alt_text(patient_name, language)
        try:
            if not fits(flex.contents.to_dict()):
                logger.warning(
                    f"{LOGGER_HEADER_TEXT} 通報卡超過大小上限，改以純文字送出"
                )
            elif await self._replier.push_flex(member_id, flex):
                return True
            else:
                logger.warning(
                    f"{LOGGER_HEADER_TEXT} Flex 推播未成功，改以純文字送出"
                )
            return bool(await self._replier.push_text(member_id, text_fallback))
        except Exception:  # noqa: BLE001
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 推播失敗，本位收件人未收到", exc_info=True
            )
            return False

    async def _recipients(self, patient_user_id: str) -> list[str]:
        """當事人本人恆不在此清單內——他收到的是自己那張紅卡。"""
        if self._authorization_service is None:
            return []
        try:
            recipients = await self._authorization_service.notification_recipients(
                patient_user_id, NOTIFICATION_KIND
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"{LOGGER_HEADER_TEXT} 收件人判定失敗，本次不通報：%s",
                type(exc).__name__,
            )
            return []
        return [uid for uid in recipients or [] if uid and uid != patient_user_id]

    async def _display_name(self, user_id: str) -> str:
        """只取名字；取不到回空字串，由呼叫端決定泛稱。"""
        if not self._user_profile_service or not user_id:
            return ""
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:  # noqa: BLE001
            return ""
        if isinstance(profile, dict) and profile.get("name"):
            return profile["name"]
        return ""

    async def _display_prefs(self, user_id: str) -> tuple[str, str, bool]:
        """逐一取收件人自己的語言、字級與通知意願。背景推播沒有 request context。"""
        if not self._user_profile_service or not user_id:
            return DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE, True
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:  # noqa: BLE001
            return DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE, True
        settings: dict = (profile or {}).get("settings") or {}
        return (
            normalize_user_language(settings.get("language")),
            normalize_user_font_size(settings.get("font_size")),
            # 欄位缺席時視為開啟——既有使用者的文件沒有這一欄，不需要 backfill。
            bool(settings.get("notify_family", True)),
        )


@dataclass(frozen=True)
class ResolvedAffected:
    """一位受影響者，加上他在發話者家庭名單中的解析結果。

    `resolution` 只有家人才會有；本人、未連結第三人與名單查詢失敗時
    為 None。解析只比對名單，不讀健康資料、不做授權——命中家人只決定怎麼稱呼，
    不代表能看他的 profile。結果只活在這一次背景任務裡，不寫回任何狀態。
    """

    person: AffectedPerson
    resolution: Optional[PersonResolution] = None

    @property
    def is_resolved_member(self) -> bool:
        return self.resolution is not None and self.resolution.kind == "member"


# 家庭名單查詢的上限。這一步在紅卡送出之後才跑，慢了不會擋到紅卡，但稱謂提示
# 晚太久就失去意義；逾時一律當成解析不到，改用中性稱謂。
RESOLVE_TIMEOUT_SECONDS = 5.0


async def resolve_affected(
    affected: tuple[AffectedPerson, ...],
    operator_id: str,
    patient_context_service: Any,
    *,
    timeout_seconds: Optional[float] = None,
) -> tuple[ResolvedAffected, ...]:
    """逐一把「家人」對到名單；任何失敗都退回未解析，不拋例外。

    認不出是誰時當成發話者本人：人物辨識失敗（例如 LLM 中斷）或模型回「不明」。
    絕大多數緊急訊息是長輩講自己，這時不通知任何人，比通知到發話者自己的家人
    更危險（2026-09-25 產品決定）。同稱謂多人與未連結第三人**不**在此列——那是
    知道是別人、只是對不到是誰，當成本人就會通知錯的家庭。
    """
    timeout_seconds = timeout_seconds or RESOLVE_TIMEOUT_SECONDS
    people = tuple(
        AffectedPerson(kind="self", event=person.event, urgent=person.urgent)
        if person.kind == "unknown"
        else person
        for person in affected
    ) or (AffectedPerson(kind="self"),)
    # 只有家人要查名單；其餘直接定案，不為他們開協程。
    lookups = {
        index: _lookup(person, operator_id, patient_context_service, timeout_seconds)
        for index, person in enumerate(people)
        if person.kind == "family" and patient_context_service is not None and operator_id
    }
    results = dict(zip(lookups, await asyncio.gather(*lookups.values()))) if lookups else {}
    return tuple(
        results.get(index) or ResolvedAffected(person) for index, person in enumerate(people)
    )


async def _lookup(
    person: AffectedPerson,
    operator_id: str,
    patient_context_service: Any,
    timeout_seconds: float,
) -> ResolvedAffected:
    try:
        resolution = await asyncio.wait_for(
            patient_context_service.resolve_person(
                operator_id,
                person=person.label,
                relationship=person.relationship or "",
            ),
            timeout=timeout_seconds,
        )
    except Exception:  # noqa: BLE001 - 含逾時；解析不到就用中性稱謂
        logger.warning(
            f"{LOGGER_HEADER_TEXT} 受影響者解析失敗，改用中性稱謂", exc_info=True
        )
        return ResolvedAffected(person)
    return ResolvedAffected(person, resolution)


def followup_texts(
    resolved: tuple[ResolvedAffected, ...], language: Optional[str] = None
) -> list[str]:
    """紅卡之後補給發話者的行動提示，稱謂依解析結果決定。

    - 只有本人（含認不出是誰、已當成本人）：不補。紅卡本來就是對發話者說的。
    - 唯一解析到的家人：用使用者自己的稱呼（「請留在阿公身邊」）。
    - 其他情況（同稱謂多人、名單查不到、朋友、路人、多位他人）：「對方」。
      叫錯人比不叫名字更糟。
    - 同一句發話者自己也有急症時，另起一句提醒，不把兩人的狀況合併。
    """
    others = [r for r in resolved if r.person.kind != "self" and r.person.urgent]
    if not others:
        return []
    texts: list[str] = []
    only = others[0] if len(others) == 1 else None
    name = ""
    if only is not None and only.is_resolved_member:
        name = only.person.label or only.resolution.display_label
    if name:
        texts.append(t("text.emergency.stay_with_named", language).format(name=name))
    else:
        texts.append(t("text.emergency.stay_with_other", language))
    if any(r.person.kind == "self" and r.person.urgent for r in resolved):
        texts.append(t("text.emergency.self_also_urgent", language))
    return texts


async def notify_patient_family_was_told(
    replier: _Replier, patient_user_id: str, language: Optional[str] = None
) -> None:
    """告訴當事人「家人已經知道了」。

    為什麼是分開的一則訊息而不是寫在紅卡上：紅卡在通報之前就送出去了（那是
    刻意的，見服務模組註解），組卡當下還不知道通報會不會成功。與其在卡上寫一句
    可能不成立的話，不如通報真的送出後再補一則。

    措辭刻意是支持性的而非警告式的。這則訊息的收件人正處於危機中，讀起來必須
    像有人來陪，不是像被舉報——否則下一次他就不說了，而那是我們最不能承受的
    後果。
    """
    try:
        await replier.push_text(
            patient_user_id, t("text.emergency.family_notified", language)
        )
    except Exception:  # noqa: BLE001
        logger.warning(f"{LOGGER_HEADER_TEXT} 告知當事人失敗", exc_info=True)
