"""
症狀對應建議科別的 Flex Message。

兩種版面，對應 SymptomTriageResult 的兩種 kind：
    suggestion 建議卡。主要科別 + 至多 3 個候選 + 逐條參考來源 + 免責。
    fallback   保底卡。明說系統無法判斷，給初診方向。

字級：
    文字大小走 theme.resolve_theme()，跟隨 UserSettings.font_size，不寫死。
    模板裡的 size 是 large 這一檔解析出來的結果，不是唯一合法值。

多語言（尚未做）：
    本卡的 UI 文案與 _reason_for() 仍寫死 zh-TW，科別名稱也還沒走
    app.i18n.messages.department_label()。緊急卡已完成 i18n，這張還沒——
    優先序如此是因為緊急卡是急救指示，看不懂的代價高得多。

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
from resources.flex_messages import theme

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

_NEARBY_PROMPT_COLOR = "#37474F"

# 追問下一步。刻意只是一句話 + 一顆 Quick Reply 按鈕，不主動索取位置：

_NEARBY_PROMPT = "是否需要搜尋附近{department}的醫院或診所？"
_NEARBY_QUICK_REPLY_TEXT = "搜尋附近的{department}"

_HEADER_TITLE = "推薦掛號科別"
_TAG_SUGGESTION = "(建議優先)"
_TAG_FALLBACK = "(不確定時的方向)"
_SOURCE_LABEL = "參考來源"

# 保底卡在標題列仍要顯示一個科別，否則版面會空一塊。用 FALLBACK_DEPARTMENTS
# 的第一個（家醫科），與 body 的候選一致。
_DEFAULT_PRIMARY = "家醫科"


def _header(primary: str, tag: str, ft: theme.FlexTheme) -> dict[str, Any]:
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
                "size": ft.body,
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
                        "size": ft.title,
                        "color": _TPL_ON_HEADER,
                        "weight": "bold",
                        "flex": 0,
                    },
                    {
                        "type": "text",
                        "text": tag,
                        "size": ft.caption,
                        "color": _TPL_HEADER_TAG_COLOR,
                        "margin": "md",
                        "weight": "bold",
                        "gravity": "bottom",
                    },
                ],
            },
        ],
    }


def _candidate_box(
    index: int, canonical: str, reason: str, ft: theme.FlexTheme
) -> dict[str, Any]:
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
                "size": ft.heading,
                "weight": "bold",
                "color": _TPL_CANDIDATE_TITLE_COLOR,
                "adjustMode": "shrink-to-fit",
            },
            {
                "type": "text",
                "text": f"理由：{reason}",
                "size": ft.body,
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


def _source_item(
    index: int, reference: SourceReference, ft: theme.FlexTheme
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "text",
        "text": f"{index}. {reference.name}「該看哪一科」對照表",
        "size": ft.caption,
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


def _source_section(
    references: tuple[SourceReference, ...], ft: theme.FlexTheme
) -> list[dict[str, Any]]:
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
                    "size": ft.caption,
                    "color": _TPL_LABEL_COLOR,
                    "weight": "bold",
                },
                *(
                    _source_item(index, reference, ft)
                    for index, reference in enumerate(references, start=1)
                ),
            ],
        },
    ]


def _nearby_prompt(primary: str, ft: theme.FlexTheme) -> dict[str, Any]:
    """候選之下、來源之上的一句追問。搭配 Quick Reply 按鈕使用。"""
    return {
        "type": "text",
        "text": _NEARBY_PROMPT.format(department=primary),
        "size": ft.body,
        "color": _NEARBY_PROMPT_COLOR,
        "weight": "bold",
        "wrap": True,
        "margin": "lg",
    }


def _nearby_quick_reply(primary: str) -> dict[str, Any]:
    """
    按鈕送出的是明確語句（「搜尋附近的皮膚科」），送出後由既有的
    `_is_nearby_department_intent()` 直接接住，科別搜尋流程一行都不用改。
    """
    text = _NEARBY_QUICK_REPLY_TEXT.format(department=primary)
    return {
        "items": [
            {
                "type": "action",
                "action": {"type": "message", "label": text[:20], "text": text},
            }
        ]
    }


def _footer(ft: theme.FlexTheme) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": _TPL_FOOTER_BG,
        "paddingAll": "12px",
        "contents": [
            {
                "type": "text",
                "text": _DISCLAIMER,
                "size": ft.caption,
                "color": _TPL_FOOTER_TEXT_COLOR,
                "weight": "bold",
                "wrap": True,
            }
        ],
    }


def _build_suggestion_bubble(
    result: SymptomTriageResult,
    references: tuple[SourceReference, ...],
    ft: theme.FlexTheme,
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
        "header": _header(primary, tag, ft),
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
                    "size": ft.body,
                    "color": _TPL_LABEL_COLOR,
                    "weight": "bold",
                    "wrap": True,
                },
                *(
                    _candidate_box(
                        index,
                        candidate.canonical,
                        _reason_for(candidate, result.matched_term),
                        ft,
                    )
                    for index, candidate in enumerate(result.candidates, start=1)
                ),
                _nearby_prompt(primary, ft),
                *_source_section(references, ft),
            ],
        },
        "footer": _footer(ft),
    }


def build_symptom_department_flex(
    result: SymptomTriageResult,
    *,
    references: tuple[SourceReference, ...] | None = None,
    font_size: str | None = None,
) -> dict[str, Any]:
    """組出可直接送往 LINE 的 Flex Message 外層結構。

    font_size 省略時讀 request-scoped 的 ContextVar（webhook 進來時由 handler
    依使用者設定寫入）；references 可注入，測試才能在不讀對照表檔的情況下
    驗證來源條列。
    """
    resolved = load_source_references() if references is None else references
    ft = theme.resolve_theme(font_size)
    primary = result.primary_department or _DEFAULT_PRIMARY
    return {
        "type": "flex",
        "altText": ALT_TEXT_SUGGESTION,
        "contents": _build_suggestion_bubble(result, resolved, ft),
        "quickReply": _nearby_quick_reply(primary),
    }
