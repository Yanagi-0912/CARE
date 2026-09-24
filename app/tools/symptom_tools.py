"""
症狀 → 建議科別的 agent tool。

與 get_rag_answer 的分界：
    「我肚子痛」問的是衛教知識，走 get_rag_answer。
    「我肚子痛要掛哪一科」問的是掛號科別，走本工具。
    差別在有沒有掛號意圖，不在有沒有症狀——兩者都有症狀。
"""

import json
import logging
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from app.core.request_context import get_line_user_id
from app.core.user_language import get_request_language, normalize_user_language
from app.i18n.messages import (
    department_label,
    subgroup_label,
    symptom_fallback_reason,
    t,
)
from app.services.family.patient_context import PatientContext
from app.services.medical.symptom_classification.symptom_department_service import (
    RESULT_FALLBACK,
    SymptomTriageResult,
)
from resources.flex_messages.medical_messages.symptom_department_flex_message import (
    build_symptom_department_flex,
    pediatric_note,
)

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Tool:suggest_department_for_symptom]"

FamilyRelationship = Literal[
    "parent",
    "child",
    "spouse",
    "sibling",
    "grandparent",
    "grandchild",
]


class SymptomPatientCase(BaseModel):
    """同一則訊息中，一位看診者與其症狀的結構化配對。"""

    model_config = ConfigDict(extra="forbid")

    symptom: str = Field(
        description="這位看診者的症狀原文；不得混入另一位人物的症狀"
    )
    person: str = Field(
        default="",
        description="明確姓名或未連結人物的稱呼；問本人時留空",
    )
    relationship: FamilyRelationship | None = Field(
        default=None,
        description="明確家人關係；沒有時省略",
    )
    age: int | None = Field(
        default=None,
        description="本輪明確屬於這位看診者的年齡",
    )
    gender: Literal["male", "female"] | None = Field(
        default=None,
        description="本輪明確屬於這位看診者的性別",
    )
    requested_department: str = Field(
        default="",
        description="使用者針對這位看診者明確點名的部定專科",
    )

_symptom_department_service = None
_patient_context_service = None


def configure_symptom_tool(symptom_department_service, patient_context_service) -> None:
    """DI 初始化時呼叫，注入科別建議與看診者脈絡服務。"""
    global _symptom_department_service, _patient_context_service
    _symptom_department_service = symptom_department_service
    _patient_context_service = patient_context_service


def _format_plain_reply(
    result: SymptomTriageResult, language: str | None = None
) -> str:
    """
    Flex 組裝失敗時的純文字 fallback，仍須符合 line-reply-rules 的不得輸出
    Markdown。呈現層出錯不該讓使用者拿到空白回覆。
    """
    lang = normalize_user_language(language or get_request_language())
    if result.kind == RESULT_FALLBACK:
        header = t("flex.symptom.plain.fallback_header", lang).format(
            reason=symptom_fallback_reason(result.fallback_reason, lang)
        )
        intro = t("flex.symptom.plain.fallback_intro", lang)
    else:
        header = t("flex.symptom.plain.suggestion_header", lang).format(
            term=result.matched_term or "你描述的狀況"
        )
        intro = t("flex.symptom.plain.suggestion_intro", lang)

    lines = [header]
    note = pediatric_note(result, lang)
    if note is not None:
        lines.append(note)
    lines.append(intro)
    for index, candidate in enumerate(result.candidates, start=1):
        suffix = ""
        if candidate.subgroups:
            alternatives = t("flex.symptom.alternative_separator", lang).join(
                subgroup_label(value, lang) for value in candidate.subgroups
            )
            suffix = t("flex.symptom.plain.subgroup", lang).format(
                subgroups=alternatives
            )
        lines.append(f"{index}. {department_label(candidate.canonical, lang)}{suffix}")
    lines.append("")
    lines.append(t("flex.symptom.disclaimer", lang))
    return "\n".join(lines)


def _patient_resolution_reply(
    context: PatientContext,
    language: str | None = None,
) -> str | None:
    """人物不唯一時先釐清；找不到的人仍可取得不讀 profile 的一般建議。"""
    if context.patient_kind == "ambiguous":
        names = t("family.directory.list_sep", language).join(
            candidate.display_label or t("family.directory.unnamed", language)
            for candidate in context.candidates
        )
        return t("flex.symptom.patient.ambiguous", language).format(names=names)
    if context.patient_kind == "conflict":
        return t("flex.symptom.patient.conflict", language).format(
            query=context.display_label
        )
    return None


@tool
async def suggest_department_for_symptom(
    cases: list[SymptomPatientCase],
) -> str:
    """當使用者描述身體不適「並且詢問該掛哪一科」時呼叫。典型句型是「我肚子痛
    要掛哪一科」「這樣該看什麼科」「頭暈要看哪一科」。回傳依公開就醫病症對照
    資料整理的建議科別方向，不做診斷。

    cases：每一位看診者各一筆，症狀、人物、年齡、性別及指定科別都必須綁在同一筆。
    一位看診者也必須使用只有一筆的陣列；同句有多位看診者時，完整列出多筆，本工具
    會先請使用者選擇要處理的人，不會共用其中任何人的資料。

    若使用者只是描述症狀、詢問衛教知識而沒有問科別（例如「肚子痛怎麼辦」
    「肚子痛要吃什麼」），請改用 get_rag_answer。
    若使用者要找附近的院所，請改用位置與科別搜尋工具。
    """
    if _symptom_department_service is None or _patient_context_service is None:
        return t("flex.symptom.unavailable")

    if len(cases) != 1:
        if len(cases) > 1:
            return t("flex.symptom.patient.multiple")
        return t("flex.symptom.patient.missing")

    operator_id = get_line_user_id()
    if not operator_id:
        return t("flex.symptom.unavailable")

    case = cases[0]
    patient_context = await _patient_context_service.resolve(
        operator_id,
        person=case.person,
        relationship=case.relationship or "",
        message_age=case.age,
        message_gender=case.gender,
    )
    resolution_reply = _patient_resolution_reply(patient_context)
    if resolution_reply is not None:
        return resolution_reply

    result = await _symptom_department_service.suggest(
        case.symptom,
        patient_context=patient_context,
        requested_department=case.requested_department,
    )
    logger.info(
        f"{LOGGER_HEADER_TEXT} kind=%s term=%r patient_kind=%s age_source=%s "
        "gender_source=%s departments=%s",
        result.kind,
        result.matched_term,
        patient_context.patient_kind,
        patient_context.age_source.value,
        patient_context.gender_source.value,
        [c.canonical for c in result.candidates],
    )
    try:
        return json.dumps(build_symptom_department_flex(result), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        logger.warning(
            f"{LOGGER_HEADER_TEXT} Flex 組裝失敗，改回純文字格式", exc_info=True
        )
        return _format_plain_reply(result)
