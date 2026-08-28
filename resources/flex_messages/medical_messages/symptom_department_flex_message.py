"""
症狀 → 建議科別的 Flex Message。

兩種版面，對應 SymptomTriageResult 的兩種 kind：
    suggestion 建議卡。主要科別 + 至多 3 個候選 + 逐條參考來源 + 免責。
    fallback   保底卡。明說系統無法判斷，給初診方向。

緊急狀況不由這裡處理：急迫度判斷擋在整個 agent 之前（urgency.py），判定為
緊急的訊息根本不會走到這個工具，因此本模組沒有緊急版面。

版面對齊 symptom_department_flex_template.json：
    這張卡的樣式以模板為準，本模組只負責把文字填進去，不自行決定顏色、間距或
    字級——所有樣式常數集中在下面的 _TPL_* 與 _CANDIDATE_PALETTE，並由
    test_symptom_department_flex.py 直接對模板逐節點比對，樣式漂掉會測試失敗。

候選卡片為什麼要交替配色：
    三張同色的卡在小螢幕上會黏成一塊，看不出「這是三個並列的選項」。交替底色
    讓邊界自己顯現，不必再加分隔線。順序即優先序，但不是輕重之分，因此兩色的
    明度刻意接近——用色相區隔，不用深淺暗示排名。

用語邊界：一律是「常見的看診方向是…」，不得出現「你應該是…」「你要掛…」。
"""

from __future__ import annotations

from typing import Any

from app.services.medical.symptom_classification.symptom_department_service import (
    RESULT_FALLBACK,
    SymptomTriageResult,
)
from app.services.medical.symptom_classification.symptom_table import (
    SourceReference,
    load_source_references,
)

ALT_TEXT_SUGGESTION = "建議的看診方向"

# --- 模板樣式常數。改這裡等同改模板，兩邊必須同步（有測試比對）---------------
_TPL_HEADER_BG = "#1E7D58"
_TPL_ON_HEADER = "#FFFFFF"
_TPL_HEADER_TAG_COLOR = "#D1E7DD"
_TPL_BODY_BG = "#FAFAFA"
_TPL_LABEL_COLOR = "#555555"
_TPL_CANDIDATE_TITLE_COLOR = "#222222"
_TPL_CANDIDATE_REASON_COLOR = "#333333"
_TPL_SEPARATOR_COLOR = "#E0E0E0"
_TPL_SOURCE_LINK_COLOR = "#1D6F8A"
_TPL_FOOTER_BG = "#F0F0F0"
_TPL_FOOTER_TEXT_COLOR = "#555555"

# 候選卡片的交替配色，(底色, 邊框色)。依序循環。
_CANDIDATE_PALETTE: tuple[tuple[str, str], ...] = (
    ("#E8F5E9", "#CFE8DC"),
    ("#FFF8E7", "#F0E4C4"),
)

_DISCLAIMER = (
    "免責聲明：本建議僅供參考，不是醫療診斷。"
    "若症狀持續或惡化，請務必儘速就醫接受專業診斷。"
)

_HEADER_TITLE = "推薦掛號科別"
_TAG_SUGGESTION = "(建議優先)"
_TAG_FALLBACK = "(不確定時的方向)"
_SOURCE_LABEL = "參考來源"

# 保底卡在標題列仍要顯示一個科別，否則版面會空一塊。用 FALLBACK_DEPARTMENTS
# 的第一個（家醫科），與 body 的候選一致。
_DEFAULT_PRIMARY = "家醫科"


def _header(primary: str, tag: str) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": _TPL_HEADER_BG,
        "paddingAll": "20px",
        "contents": [
            {
                "type": "text",
                "text": _HEADER_TITLE,
                "color": _TPL_ON_HEADER,
                "size": "lg",
                "weight": "bold",
            },
            {
                "type": "box",
                "layout": "horizontal",
                "margin": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": primary,
                        "size": "3xl",
                        "color": _TPL_ON_HEADER,
                        "weight": "bold",
                        "flex": 0,
                    },
                    {
                        "type": "text",
                        "text": tag,
                        "size": "md",
                        "color": _TPL_HEADER_TAG_COLOR,
                        "margin": "md",
                        "weight": "bold",
                        "gravity": "bottom",
                    },
                ],
            },
        ],
    }


def _candidate_box(index: int, canonical: str, reason: str) -> dict[str, Any]:
    background, border = _CANDIDATE_PALETTE[(index - 1) % len(_CANDIDATE_PALETTE)]
    return {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "backgroundColor": background,
        "paddingAll": "16px",
        "cornerRadius": "8px",
        "borderWidth": "1px",
        "borderColor": border,
        "contents": [
            {
                "type": "text",
                "text": f"{index}. {canonical}",
                "size": "xl",
                "weight": "bold",
                "color": _TPL_CANDIDATE_TITLE_COLOR,
                "adjustMode": "shrink-to-fit",
            },
            {
                "type": "text",
                "text": f"理由：{reason}",
                "size": "md",
                "color": _TPL_CANDIDATE_REASON_COLOR,
                "wrap": True,
                "margin": "xs",
            },
        ],
    }


def _reason_for(candidate, matched_term: str | None) -> str:
    """
    候選科別的說明文字。刻意描述「這一科處理什麼」，不宣稱使用者得了什麼。
    """
    term = matched_term or "你描述的狀況"
    if candidate.subgroup:
        base = (
            f"{term}在這類分科中通常由{candidate.canonical}的"
            f"{candidate.subgroup}方向處理。"
        )
    else:
        base = f"{term}常見的看診方向之一是{candidate.canonical}。"

    if candidate.source_count >= 3:
        base += "（三家醫院的對照表都這樣分類）"
    elif candidate.source_count == 1:
        base += "（僅一家醫院的對照表這樣分類，建議先去電確認）"

    if candidate.note:
        base += f" 註：{candidate.note}"
    return base


def _source_item(index: int, reference: SourceReference) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "text",
        "text": f"{index}. {reference.name}「該看哪一科」對照表",
        "size": "sm",
        "color": _TPL_SOURCE_LINK_COLOR,
        "weight": "bold",
        "wrap": True,
        "action": {
            "type": "uri",
            "label": f"開啟參考網址{index}",
            "uri": reference.url,
        },
    }
    if index > 1:
        # 模板的第一條不帶 margin，其後每條 xs——條列之間要有呼吸，但不能大到
        # 看起來像是另一個區塊。
        node["margin"] = "xs"
    return node


def _source_section(references: tuple[SourceReference, ...]) -> list[dict[str, Any]]:
    """
    參考來源。逐條列出且各自可點，不把三家醫院擠成一段敘述——來源存在的目的是
    讓使用者能自己去核對，擠成一坨文字等於既點不了也記不住。
    """
    if not references:
        # fail-soft：來源讀不到時整段不出現，不留一個空標題。
        return []
    return [
        {"type": "separator", "margin": "lg", "color": _TPL_SEPARATOR_COLOR},
        {
            "type": "box",
            "layout": "vertical",
            "margin": "md",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": _SOURCE_LABEL,
                    "size": "sm",
                    "color": _TPL_LABEL_COLOR,
                    "weight": "bold",
                },
                *(
                    _source_item(index, reference)
                    for index, reference in enumerate(references, start=1)
                ),
            ],
        },
    ]


def _footer() -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": _TPL_FOOTER_BG,
        "paddingAll": "12px",
        "contents": [
            {
                "type": "text",
                "text": _DISCLAIMER,
                "size": "sm",
                "color": _TPL_FOOTER_TEXT_COLOR,
                "weight": "bold",
                "wrap": True,
            }
        ],
    }


def _build_suggestion_bubble(
    result: SymptomTriageResult, references: tuple[SourceReference, ...]
) -> dict[str, Any]:
    is_fallback = result.kind == RESULT_FALLBACK
    primary = result.primary_department or _DEFAULT_PRIMARY

    if is_fallback:
        label = (
            f"系統無法判斷你描述的狀況該掛哪一科（{result.fallback_reason}），"
            "以下是常見的初診方向"
        )
        tag = _TAG_FALLBACK
    else:
        label = f"依「{result.matched_term}」整理的可能科別與評估原因"
        tag = _TAG_SUGGESTION

    return {
        "type": "bubble",
        "size": "mega",
        "header": _header(primary, tag),
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingTop": "20px",
            "paddingBottom": "20px",
            "paddingStart": "8px",
            "paddingEnd": "20px",
            "spacing": "md",
            "backgroundColor": _TPL_BODY_BG,
            "contents": [
                {
                    "type": "text",
                    "text": label,
                    "size": "md",
                    "color": _TPL_LABEL_COLOR,
                    "weight": "bold",
                    "wrap": True,
                },
                *(
                    _candidate_box(
                        index,
                        candidate.canonical,
                        _reason_for(candidate, result.matched_term),
                    )
                    for index, candidate in enumerate(result.candidates, start=1)
                ),
                *_source_section(references),
            ],
        },
        "footer": _footer(),
    }


def build_symptom_department_flex(
    result: SymptomTriageResult,
    *,
    references: tuple[SourceReference, ...] | None = None,
) -> dict[str, Any]:
    """組出可直接送往 LINE 的 Flex Message 外層結構。

    references 可注入，測試才能在不讀對照表檔的情況下驗證來源條列。
    """
    resolved = load_source_references() if references is None else references
    return {
        "type": "flex",
        "altText": ALT_TEXT_SUGGESTION,
        "contents": _build_suggestion_bubble(result, resolved),
    }
