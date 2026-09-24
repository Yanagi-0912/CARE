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
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Optional, Protocol

from app.core.user_font_size import DEFAULT_USER_FONT_SIZE, normalize_user_font_size
from app.core.user_language import DEFAULT_USER_LANGUAGE, normalize_user_language
from app.i18n.messages import t
from app.models.safety import EmergencyReportEntry
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

# 一次通報的結果。發話者看到的文案依它選（10.17），只有 sent 可以說家人收到了。
#   sent          至少一位收件人真的收到
#   no_recipient  病人沒有合格收件人（政策篩完是空的）
#   disabled      有收件人，但全都關閉了家人通知
#   failed        收件人判定失敗、推播失敗、或任何未預期的例外
NotifyOutcome = Literal["sent", "no_recipient", "disabled", "failed"]
# 通報前被擋下的兩種結果（10.18）。發話者同樣依它選文案。
#   duplicate     同一位病人 10 分鐘內已經通報送達過
#   rate_limited  替別人回報超過頻率上限
ReportOutcome = Literal[
    "sent", "no_recipient", "disabled", "failed", "duplicate", "rate_limited"
]

# 去重：同一位病人 10 分鐘內只通報一次，不論是誰回報、說的是不是同一件事——事件
# 內容是 LLM 的轉述，每次都不同，無法可靠比對（2026-09-25 決定以病人為準）。
DEDUPE_MINUTES = 10
CLAIM_KEY = NOTIFICATION_KIND
# 頻率限制：只限替別人回報。本人急症只去重、不設上限——長輩真的連續出狀況時，
# 不該因為次數多就收不到。只數真的送達的通報。
REPORTER_LIMIT = (5, timedelta(hours=24))
PATIENT_LIMIT = (3, timedelta(hours=1))
AUDIT_RETENTION = timedelta(days=60)


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
        report_repository: Any = None,
        claim_repository: Any = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._replier = replier
        self._authorization_service = authorization_service
        self._user_profile_service = user_profile_service
        # 沒注入時不留稽核、不去重、不限流（測試與舊組裝），通報照常。
        self._reports = report_repository
        self._claims = claim_repository
        self._now = clock or (lambda: datetime.now(timezone.utc))

    async def report(
        self,
        reporter_id: str,
        resolved: tuple[ResolvedAffected, ...],
        reason: str,
        words: str = "",
    ) -> dict[str, ReportOutcome]:
        """一次緊急判定的完整通報：選病人 → 限流 → 去重 → 通知 → 寫稽核。

        回傳每位實際處理過的病人的結果；不在結果裡的人就是沒有通知（第三人、
        同稱謂多人、連結未確認）。任何一步失敗都不影響其他病人，也不拋出。
        """
        outcomes: dict[str, ReportOutcome] = {}
        try:
            patients = await self.patients_to_notify(reporter_id, resolved)
        except Exception:  # noqa: BLE001
            logger.error(f"{LOGGER_HEADER_TEXT} 通知對象判定失敗", exc_info=True)
            patients = []
        for patient_id in patients:
            outcomes[patient_id] = await self._report_one(
                patient_id, reporter_id, reason, words
            )
        await self._audit(reporter_id, resolved, outcomes, reason)
        return outcomes

    async def _report_one(
        self, patient_id: str, reporter_id: str, reason: str, words: str
    ) -> ReportOutcome:
        cross_person = patient_id != reporter_id
        # 先限流再去重：被限流的那次沒有送出，不能佔住去重名額，否則下一次會被
        # 說成「剛才已通知過」。
        if cross_person and await self._over_limit(reporter_id, patient_id):
            logger.warning(f"{LOGGER_HEADER_TEXT} 替別人回報超過頻率上限，本次不通報")
            return "rate_limited"
        if not await self._claim(patient_id):
            logger.info(f"{LOGGER_HEADER_TEXT} 同一位病人 {DEDUPE_MINUTES} 分鐘內已通報")
            return "duplicate"
        outcome = await self.notify(patient_id, reason, words, reporter_id=reporter_id)
        if outcome != "sent":
            # 沒有送到任何人：交還去重名額，下一次才不會被當成「已通知過」擋下。
            await self._release(patient_id)
        return outcome

    async def _over_limit(self, reporter_id: str, patient_id: str) -> bool:
        """失敗時不限流：寧可多送一次，也不要因為計數查不到而擋掉急症通報。"""
        if self._reports is None:
            return False
        now = self._now()
        try:
            by_reporter = await self._reports.count_cross_person_sent(
                reporter_id=reporter_id, since=now - REPORTER_LIMIT[1]
            )
            if by_reporter >= REPORTER_LIMIT[0]:
                return True
            by_patient = await self._reports.count_cross_person_sent(
                patient_id=patient_id, since=now - PATIENT_LIMIT[1]
            )
        except Exception:  # noqa: BLE001
            logger.warning(f"{LOGGER_HEADER_TEXT} 頻率計數失敗，本次不限流", exc_info=True)
            return False
        return by_patient >= PATIENT_LIMIT[0]

    async def _claim(self, patient_id: str) -> bool:
        """取得去重名額。失敗時放行，理由同 _over_limit。"""
        if self._claims is None:
            return True
        try:
            return await self._claims.try_claim(patient_id, CLAIM_KEY, DEDUPE_MINUTES)
        except Exception:  # noqa: BLE001
            logger.warning(f"{LOGGER_HEADER_TEXT} 去重名額取得失敗，本次照常通報", exc_info=True)
            return True

    async def _release(self, patient_id: str) -> None:
        if self._claims is None:
            return
        try:
            await self._claims.release(patient_id, CLAIM_KEY)
        except Exception:  # noqa: BLE001
            logger.warning(f"{LOGGER_HEADER_TEXT} 去重名額交還失敗", exc_info=True)

    async def _audit(
        self,
        reporter_id: str,
        resolved: tuple[ResolvedAffected, ...],
        outcomes: dict[str, ReportOutcome],
        reason: str,
    ) -> None:
        """每位急症者寫一筆，含沒有通知的人。寫不進去只記 log，不影響通報。"""
        if self._reports is None:
            return
        now = self._now()
        report_id = uuid.uuid4().hex
        entries = [
            _audit_entry(item, reporter_id, outcomes, reason, report_id, now)
            for item in resolved
            if item.person.urgent
        ]
        try:
            await self._reports.append_many(entries)
        except Exception:  # noqa: BLE001
            logger.error(f"{LOGGER_HEADER_TEXT} 緊急回報稽核寫入失敗", exc_info=True)

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
    ) -> NotifyOutcome:
        """對外的唯一入口。回傳這次通報的結果；任何失敗都吞在這裡，回 failed。

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
            return "failed"
        try:
            return await self._notify(
                patient_user_id, reason, words, reporter_id=reporter_id
            )
        except Exception:  # noqa: BLE001
            logger.error(
                f"{LOGGER_HEADER_TEXT} 家人通報失敗，當事人的回覆不受影響",
                exc_info=True,
            )
            return "failed"

    async def _notify(
        self,
        patient_user_id: str,
        reason: str,
        words: str = "",
        *,
        reporter_id: str = "",
    ) -> NotifyOutcome:
        eligible = await self._recipients(patient_user_id)
        if eligible is None:
            return "failed"
        recipients = [uid for uid in eligible if uid != reporter_id]
        if not recipients:
            logger.info(f"{LOGGER_HEADER_TEXT} 沒有合格收件人，本次不通報")
            return "no_recipient"

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
        disabled = 0
        for member_id in recipients:
            language, font_size, notify_family = await self._display_prefs(member_id)
            if not notify_family:
                # 抑制的只有推播。判定本身仍然發生過，當事人也已經收到紅卡。
                logger.info(
                    f"{LOGGER_HEADER_TEXT} 收件人關閉了家人通知，略過推播"
                )
                disabled += 1
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

        outcome: NotifyOutcome = (
            "sent" if sent else "disabled" if disabled == len(recipients) else "failed"
        )
        logger.info(f"{LOGGER_HEADER_TEXT} 通報完成，outcome=%s", outcome)
        return outcome

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

    async def _recipients(self, patient_user_id: str) -> Optional[list[str]]:
        """當事人本人恆不在此清單內——他收到的是自己那張紅卡。

        判定失敗回 None，與「沒有收件人」分開：前者是系統出錯，不能對發話者說成
        「沒有設定可接收通知的家人」。
        """
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
            return None
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


def _audit_entry(
    item: ResolvedAffected,
    reporter_id: str,
    outcomes: dict[str, ReportOutcome],
    reason: str,
    report_id: str,
    now: datetime,
) -> EmergencyReportEntry:
    person = item.person
    resolution_kind = item.resolution.kind if item.resolution is not None else None
    if person.kind == "self":
        patient_id: Optional[str] = reporter_id
        outcome = outcomes.get(reporter_id, "not_notified")
    elif item.is_resolved_member and item.resolution.member is not None:
        patient_id = item.resolution.member.user_id
        outcome = outcomes.get(patient_id, "not_linked")
    else:
        patient_id = None
        outcome = "not_notified"
    return EmergencyReportEntry(
        report_id=report_id,
        reporter_id=reporter_id,
        patient_id=patient_id,
        # resolve_affected 已把「不明」轉成本人，這裡只剩三種。
        person_kind=person.kind if person.kind != "unknown" else "self",
        label=person.label,
        resolution_kind=resolution_kind,
        cross_person=patient_id is not None and patient_id != reporter_id,
        outcome=outcome,
        reason=reason,
        reported_at=now,
        expires_at=now + AUDIT_RETENTION,
    )


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
    resolved: tuple[ResolvedAffected, ...],
    outcomes: dict[str, ReportOutcome],
    reporter_id: str,
    language: Optional[str] = None,
) -> list[str]:
    """紅卡之後給發話者的一則訊息：通知結果加上稱謂正確的行動提示。

    每位急症者一行，文案依「人物種類 × 通知結果」選固定字串：
    - 本人：依 outcomes[發話者] 說明家人有沒有收到。
    - 唯一解析的家人：用使用者自己的稱呼（「阿公」），依 outcomes[家人] 說明；
      沒有結果（連結未經驗證而沒有通知）時明說沒有自動通知。
    - 其他人（同稱謂多人、名單查不到、朋友、路人）：合成一行「對方」，明說沒有
      自動通知。叫錯人比不叫名字更糟。
    只有 sent 可以說家人收到了；duplicate 說「剛才已通知過」，其餘一律不得宣稱已送達。
    同一句發話者自己也有急症、又有其他急症者時，另起一句提醒他一併告訴 119。
    """
    urgent = [r for r in resolved if r.person.urgent]
    others = [r for r in urgent if r.person.kind != "self"]
    texts: list[str] = []
    neutral = False
    for item in others:
        if not (item.is_resolved_member and item.resolution.member is not None):
            neutral = True
            continue
        name = item.person.label or item.resolution.display_label
        outcome = outcomes.get(item.resolution.member.user_id, "not_notified")
        texts.append(
            t(f"text.emergency.result.member.{outcome}", language).format(name=name)
        )
    if neutral:
        texts.append(t("text.emergency.result.other.not_notified", language))
    if any(r.person.kind == "self" for r in urgent):
        # 通知服務沒接上時（outcomes 裡沒有發話者）照 failed 說：沒有送到就不能說送到。
        outcome = outcomes.get(reporter_id, "failed")
        texts.append(t(f"text.emergency.result.self.{outcome}", language))
        if others:
            texts.append(t("text.emergency.self_also_urgent", language))
    return texts
