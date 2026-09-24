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

from app.core.request_context import get_line_user_id
from app.core.user_language import get_request_language, normalize_user_language
from app.i18n.messages import (
    department_label,
    subgroup_label,
    symptom_fallback_reason,
    t,
)
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
    "",
    "parent",
    "child",
    "spouse",
    "sibling",
    "grandparent",
    "grandchild",
]

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


@tool
async def suggest_department_for_symptom(
    symptom: str,
    person: str = "",
    relationship: FamilyRelationship = "",
    age: int | None = None,
    gender: Literal["male", "female"] | None = None,
) -> str:
    """當使用者描述身體不適「並且詢問該掛哪一科」時呼叫。典型句型是「我肚子痛
    要掛哪一科」「這樣該看什麼科」「頭暈要看哪一科」。回傳依公開就醫病症對照
    資料整理的建議科別方向，不做診斷。

    symptom：使用者描述的症狀原文（例如「肚子好痛」），不得改寫成醫學名詞。
    person：明確姓名；未提供姓名或問本人時留空。
    relationship：只填 parent、child、spouse、sibling、grandparent、grandchild
    其中之一；沒有明確家人關係時留空。
    age／gender：只填本輪訊息明確屬於看診者的年齡與性別；不得從稱謂猜測。

    若使用者只是描述症狀、詢問衛教知識而沒有問科別（例如「肚子痛怎麼辦」
    「肚子痛要吃什麼」），請改用 get_rag_answer。
    若使用者要找附近的院所，請改用位置與科別搜尋工具。
    """
    if _symptom_department_service is None or _patient_context_service is None:
        return t("flex.symptom.unavailable")

    operator_id = get_line_user_id()
    if not operator_id:
        return t("flex.symptom.unavailable")

    patient_context = await _patient_context_service.resolve(
        operator_id,
        person=person,
        relationship=relationship,
        message_age=age,
        message_gender=gender,
    )
    result = await _symptom_department_service.suggest(
        symptom,
        patient_context=patient_context,
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
