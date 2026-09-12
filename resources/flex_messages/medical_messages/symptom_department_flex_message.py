"""
症狀對應建議科別的 Flex Message。

兩種版面，對應 SymptomTriageResult 的兩種 kind：
    suggestion 建議卡。主要科別 + 至多 MAX_CANDIDATES 個候選（各附來源標註）
               + 逐條參考來源 + 免責。
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
_TPL_SUBGROUP_CHIP_BG = "#1E7D58"
_TPL_SUBGROUP_CHIP_TEXT = "#FFFFFF"
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


def _subgroup_chip(subgroup: str, ft: theme.FlexTheme) -> dict[str, Any]:
    #次專科標籤。深綠底白字，與候選卡的淺色底拉出對比。

    return {
        "type": "box",
        "layout": "vertical",
        "flex": 0,
        "backgroundColor": _TPL_SUBGROUP_CHIP_BG,
        "cornerRadius": "14px",
        "paddingAll": "4px",
        "paddingStart": "10px",
        "paddingEnd": "10px",
        "contents": [
            {
                "type": "text",
                "text": subgroup,
                "size": ft.caption,
                "color": _TPL_SUBGROUP_CHIP_TEXT,
                "weight": "bold",
                "align": "center",
            }
        ],
    }


def _candidate_title(
    index: int, canonical: str, subgroups: tuple[str, ...], ft: theme.FlexTheme
) -> dict[str, Any]:
    """候選卡的標題。有次專科時是「科別 + 標籤」一行，沒有時就只是一行文字。
    次專科有幾個就掛幾顆標籤
    """
    title: dict[str, Any] = {
        "type": "text",
        "text": f"{index}. {canonical}",
        "size": ft.heading,
        "weight": "bold",
        "color": _TPL_CANDIDATE_TITLE_COLOR,
        "adjustMode": "shrink-to-fit",
    }
    if not subgroups:
        return title
    return {
        "type": "box",
        "layout": "horizontal",
        "spacing": "sm",
        "alignItems": "center",
        "contents": [
            {**title, "flex": 0},
            *(_subgroup_chip(subgroup, ft) for subgroup in subgroups),
        ],
    }


def _candidate_box(
    index: int,
    canonical: str,
    subgroups: tuple[str, ...],
    reason: str,
    ft: theme.FlexTheme,
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
            _candidate_title(index, canonical, subgroups, ft),
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


def _source_annotation(candidate, hospital_count: int) -> str:
    """
    候選的來源標註（design 決策 15）。N＝收錄此症狀的醫院數，M＝列在這一科的醫院數。

    不寫死醫院總數，也不說「都」：來源會增加，「三家醫院都這樣分類」在第四家
    併入後就成了錯話。保底候選沒有來源，不加註。
    """
    listed = candidate.source_count
    if listed == 0:
        return ""
    if hospital_count == 1:
        return "（僅 1 家醫院的對照表收錄此症狀，建議先去電確認）"
    call_ahead = "，建議先去電確認" if listed == 1 else ""
    return f"（收錄此症狀的 {hospital_count} 家醫院中，有 {listed} 家列在此科{call_ahead}）"


def _reason_for(candidate, matched_term: str | None, hospital_count: int) -> str:
    """
    候選科別的說明文字。刻意描述「這一科處理什麼」，不宣稱使用者得了什麼。

    對照表的 note 是維護紀錄，不在這裡出現。
    """
    term = matched_term or "你描述的狀況"
    if candidate.subgroups:
        # 多個次專科用「或」連接而不是頓號：頓號讀起來像「兩個都要看」，
        # 但那是兩條擇一的路（漏斗胸：成人走胸腔外科、小孩走小兒外科）。
        directions = "或".join(candidate.subgroups)
        base = (
            f"{term}在這類分科中通常由{candidate.canonical}的"
            f"{directions}方向處理。"
        )
    else:
        base = f"{term}常見的看診方向之一是{candidate.canonical}。"
    return base + _source_annotation(candidate, hospital_count)


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


def _cited_references(
    result: SymptomTriageResult, references: tuple[SourceReference, ...]
) -> tuple[SourceReference, ...]:
    """
    只留下至少支持一個已顯示候選的來源。

    被兒科濾掉的候選不算：列出它的來源，等於說那家醫院支持這張卡上的科別。
    保底卡的候選沒有來源，因此不會有出處。
    """
    cited = {code for candidate in result.candidates for code in candidate.sources}
    # 維持 references 的原始順序，不依 cited 的集合順序（那是不穩定的）。
    return tuple(ref for ref in references if ref.code in cited)


def _source_section(
    references: tuple[SourceReference, ...], label: str, ft: theme.FlexTheme
) -> list[dict[str, Any]]:
    """
    參考來源。逐條列出且各自可點，不把醫院擠成一段敘述——來源存在的目的是
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
                    "text": label,
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

    # 標註的分母：收錄這個症狀的醫院數。候選自己的來源一併計入——列在這一科的
    # 醫院必然收錄了這個症狀，呼叫端漏帶 term_sources 時分母才不會小於分子。
    hospital_count = len(
        set(result.term_sources).union(*(c.sources for c in result.candidates))
    )

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
                        candidate.subgroups,
                        _reason_for(candidate, result.matched_term, hospital_count),
                        ft,
                    )
                    for index, candidate in enumerate(result.candidates, start=1)
                ),
                _nearby_prompt(primary, ft),
                *_source_section(references, _SOURCE_LABEL, ft),
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
    驗證來源條列。實際列出的是這些來源中「真的收錄了這個症狀」的那幾家
    （見 _cited_references）。
    """
    resolved = load_source_references() if references is None else references
    resolved = _cited_references(result, resolved)
    ft = theme.resolve_theme(font_size)
    primary = result.primary_department or _DEFAULT_PRIMARY
    return {
        "type": "flex",
        "altText": ALT_TEXT_SUGGESTION,
        "contents": _build_suggestion_bubble(result, resolved, ft),
        "quickReply": _nearby_quick_reply(primary),
    }
