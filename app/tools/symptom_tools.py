"""
症狀 → 建議科別的 agent tool。

與 get_rag_answer 的分界：
    「我肚子痛」問的是衛教知識，走 get_rag_answer。
    「我肚子痛要掛哪一科」問的是掛號科別，走本工具。
    差別在有沒有掛號意圖，不在有沒有症狀——兩者都有症狀。
"""

import json
import logging

from langchain_core.tools import tool

from app.services.medical.symptom_classification.symptom_department_service import (
    RESULT_FALLBACK,
    SymptomTriageResult,
)
from resources.flex_messages.medical_messages.symptom_department_flex_message import (
    build_symptom_department_flex,
)

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Tool:suggest_department_for_symptom]"

_symptom_department_service = None


def configure_symptom_tool(symptom_department_service) -> None:
    """DI 初始化時呼叫，注入 SymptomDepartmentService 實例。"""
    global _symptom_department_service
    _symptom_department_service = symptom_department_service


def _format_plain_reply(result: SymptomTriageResult) -> str:
    """
    Flex 組裝失敗時的純文字 fallback，仍須符合 line-reply-rules 的不得輸出
    Markdown。呈現層出錯不該讓使用者拿到空白回覆。
    """
    if result.kind == RESULT_FALLBACK:
        header = f"系統無法判斷你描述的狀況該掛哪一科（{result.fallback_reason}）。"
        intro = "不確定時常見的初診方向："
    else:
        header = f"依「{result.matched_term}」整理的看診方向："
        intro = "常見的看診方向："

    lines = [header, intro]
    for index, candidate in enumerate(result.candidates, start=1):
        suffix = f"（{candidate.subgroup}方向）" if candidate.subgroup else ""
        lines.append(f"{index}. {candidate.canonical}{suffix}")
    lines.append("")
    lines.append(
        "以上僅供選擇科別時參考，不是醫療診斷。症狀持續、變化或加劇時請儘速就醫。"
    )
    return "\n".join(lines)


@tool
async def suggest_department_for_symptom(symptom: str) -> str:
    """當使用者描述身體不適「並且詢問該掛哪一科」時呼叫。典型句型是「我肚子痛
    要掛哪一科」「這樣該看什麼科」「頭暈要看哪一科」。回傳依公開就醫病症對照
    資料整理的建議科別方向，不做診斷。

    參數 symptom 請填入使用者描述的症狀原文（例如「肚子好痛」），不要自行
    改寫成醫學名詞，也不要填入科別名稱。

    若使用者只是描述症狀、詢問衛教知識而沒有問科別（例如「肚子痛怎麼辦」
    「肚子痛要吃什麼」），請改用 get_rag_answer。
    若使用者要找附近的院所，請改用位置與科別搜尋工具。
    """
    if _symptom_department_service is None:
        return "科別建議服務未初始化，請稍後再試。"

    result = await _symptom_department_service.suggest(symptom)
    logger.info(
        f"{LOGGER_HEADER_TEXT} kind=%s term=%r departments=%s",
        result.kind,
        result.matched_term,
        [c.canonical for c in result.candidates],
    )
    try:
        return json.dumps(build_symptom_department_flex(result), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        logger.warning(
            f"{LOGGER_HEADER_TEXT} Flex 組裝失敗，改回純文字格式", exc_info=True
        )
        return _format_plain_reply(result)
