"""
症狀對應建議科別的 Flex Message。

兩種版面，對應 SymptomTriageResult 的兩種 kind：
    suggestion 建議卡。主要科別 + 至多 MAX_CANDIDATES 個候選（各附來源標註）
               + 逐條參考來源 + 免責。
    fallback   保底卡。明說系統無法判斷，給初診方向；追問與按鈕一次涵蓋全部方向。
               為孩童詢問時多列兒科，並說明為什麼多列。

字級：
    文字大小走 theme.resolve_theme()，跟隨 UserSettings.font_size，不寫死。
    模板裡的 size 是 large 這一檔解析出來的結果，不是唯一合法值。

多語言：
    固定文案、主科別與次專科均走 app.i18n.messages；症狀對照表的命中詞維持
    中文資料來源，但非中文卡不直接顯示它，避免在外語句子中夾入未翻譯症狀。

"""

from __future__ import annotations

from typing import Any

from app.core.user_language import get_request_language, normalize_user_language
from app.core.user_age import PEDIATRIC_AGE_LIMIT
from app.i18n.messages import (
    department_label,
    subgroup_label,
    symptom_fallback_reason,
    t,
)
from app.services.medical.symptom_classification.symptom_department_service import (
    PEDIATRIC_REASON_AGE,
    PEDIATRIC_REASON_MENTIONED_CHILD,
    RESULT_FALLBACK,
    SymptomTriageResult,
)
from app.services.medical.symptom_classification.symptom_table import (
    SourceReference,
    load_source_references,
)
from resources.flex_messages import theme

# 卡片頂層的科別標記。對話紀錄存的是整張卡的 JSON，摘要靠這個 key 取得卡片種類與
# 建議科別，不必從卡片節點反解文字；送往 LINE 時 replier 只取 altText／contents，不會帶出去。
SYMPTOM_DEPARTMENT_KEY = "symptomDepartment"

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

_NEARBY_PROMPT_COLOR = "#37474F"


def _header(
    primary: str, tag: str, ft: theme.FlexTheme, language: str
) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": _TPL_HEADER_BG,
        "paddingAll": "20px",
        "contents": [
            {
                "type": "text",
                "text": t("flex.symptom.header", language),
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
    language: str,
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
                "text": t("flex.symptom.reason.label", language).format(reason=reason),
                "size": ft.body,
                "color": _TPL_CANDIDATE_REASON_COLOR,
                "wrap": True,
                "margin": "xs",
            },
        ],
    }


def _source_annotation(candidate, hospital_count: int, language: str) -> str:
    """
    候選的來源標註（design 決策 15）。N＝收錄此症狀的醫院數，M＝列在這一科的醫院數。

    不寫死醫院總數，也不說「都」：來源會增加，「三家醫院都這樣分類」在第四家
    併入後就成了錯話。保底候選沒有來源，不加註。
    """
    listed = candidate.source_count
    if listed == 0:
        return ""
    if hospital_count == 1:
        return t("flex.symptom.source.single", language)
    call_ahead = t("flex.symptom.source.call_ahead", language) if listed == 1 else ""
    return t("flex.symptom.source.multiple", language).format(
        hospital_count=hospital_count,
        listed=listed,
        call_ahead=call_ahead,
    )


def _reason_for(
    candidate, matched_term: str | None, hospital_count: int, language: str
) -> str:
    """
    候選科別的說明文字。刻意描述「這一科處理什麼」，不宣稱使用者得了什麼。

    對照表的 note 是維護紀錄，不在這裡出現。
    """
    term = matched_term or "你描述的狀況"
    department = department_label(candidate.canonical, language)
    if candidate.subgroups:
        # 多個次專科用「或」連接而不是頓號：頓號讀起來像「兩個都要看」，
        # 但那是兩條擇一的路（漏斗胸：成人走胸腔外科、小孩走小兒外科）。
        directions = t("flex.symptom.alternative_separator", language).join(
            subgroup_label(value, language) for value in candidate.subgroups
        )
        base = t("flex.symptom.reason.subgroup", language).format(
            term=term,
            department=department,
            subgroups=directions,
        )
    else:
        base = t("flex.symptom.reason.department", language).format(
            term=term,
            department=department,
        )
    return base + _source_annotation(candidate, hospital_count, language)


def _source_item(
    index: int, reference: SourceReference, ft: theme.FlexTheme, language: str
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "text",
        "text": t("flex.symptom.source.item", language).format(
            index=index, name=reference.name
        ),
        "size": ft.caption,
        "color": _TPL_SOURCE_LINK_COLOR,
        "weight": "bold",
        "wrap": True,
        "action": {
            "type": "uri",
            "label": t("flex.symptom.source.open", language).format(index=index),
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
    references: tuple[SourceReference, ...], ft: theme.FlexTheme, language: str
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
                    "text": t("flex.symptom.source.label", language),
                    "size": ft.caption,
                    "color": _TPL_LABEL_COLOR,
                    "weight": "bold",
                },
                *(
                    _source_item(index, reference, ft, language)
                    for index, reference in enumerate(references, start=1)
                ),
            ],
        },
    ]


def pediatric_note(
    result: SymptomTriageResult, language: str | None = None
) -> str | None:
    """保底多列兒科時的說明；卡片與純文字回覆共用這一份文案。"""
    if result.pediatric_reason is None:
        return None
    lang = normalize_user_language(language or get_request_language())
    if result.pediatric_reason == PEDIATRIC_REASON_MENTIONED_CHILD:
        return t("flex.symptom.pediatric.mentioned", lang)
    return t("flex.symptom.pediatric.age", lang).format(age=PEDIATRIC_AGE_LIMIT)


def _nearby_departments(result: SymptomTriageResult) -> tuple[str, ...]:
    """按鈕要搜尋的科別：建議卡是第一順位那一科，保底卡是卡上列出的全部初診方向。"""
    if result.kind == RESULT_FALLBACK:
        return tuple(c.canonical for c in result.candidates)
    return (result.primary_department,)


def _localized_departments(departments: tuple[str, ...], language: str) -> str:
    separator = t("consultation_card.list_separator", language)
    return separator.join(department_label(value, language) for value in departments)


def _nearby_prompt(
    result: SymptomTriageResult, ft: theme.FlexTheme, language: str
) -> dict[str, Any]:
    """候選之下、來源之上的一句追問。搭配 Quick Reply 按鈕使用。"""
    departments = _localized_departments(_nearby_departments(result), language)
    if result.kind == RESULT_FALLBACK:
        text = t("flex.symptom.nearby.fallback_prompt", language).format(
            departments=departments
        )
    else:
        text = t("flex.symptom.nearby.prompt", language).format(
            department=departments
        )
    return {
        "type": "text",
        "text": text,
        "size": ft.body,
        "color": _NEARBY_PROMPT_COLOR,
        "weight": "bold",
        "wrap": True,
        "margin": "lg",
    }


def _nearby_quick_reply(
    departments: tuple[str, ...], language: str
) -> dict[str, Any]:
    """
    按鈕送出的是明確語句（「搜尋附近的皮膚科」「搜尋附近的家醫科、內科、不分科」），
    送出後由既有的 `_is_nearby_department_intent()` 接住，列舉的科別會一起帶進搜尋。
    """
    localized = _localized_departments(departments, language)
    text = t("flex.symptom.nearby.button", language).format(departments=localized)
    return {
        "items": [
            {
                "type": "action",
                "action": {"type": "message", "label": text[:20], "text": text},
            }
        ]
    }


def _footer(ft: theme.FlexTheme, language: str) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": _TPL_FOOTER_BG,
        "paddingAll": "12px",
        "contents": [
            {
                "type": "text",
                "text": t("flex.symptom.disclaimer", language),
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
    language: str,
) -> dict[str, Any]:
    is_fallback = result.kind == RESULT_FALLBACK
    primary = department_label(result.primary_department, language)

    if is_fallback:
        label = t("flex.symptom.body.fallback", language).format(
            reason=symptom_fallback_reason(result.fallback_reason, language)
        )
        note = pediatric_note(result, language)
        if note is not None:
            label = f"{label}{t('flex.symptom.sentence_separator', language)}{note}"
        tag = t("flex.symptom.tag.fallback", language)
    else:
        label = t("flex.symptom.body.suggestion", language).format(
            term=result.matched_term or "你描述的狀況"
        )
        tag = t("flex.symptom.tag.suggestion", language)

    # 標註的分母：收錄這個症狀的醫院數。候選自己的來源一併計入——列在這一科的
    # 醫院必然收錄了這個症狀，呼叫端漏帶 term_sources 時分母才不會小於分子。
    hospital_count = len(
        set(result.term_sources).union(*(c.sources for c in result.candidates))
    )

    return {
        "type": "bubble",
        "size": "mega",
        "header": _header(primary, tag, ft, language),
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
                        department_label(candidate.canonical, language),
                        tuple(subgroup_label(value, language) for value in candidate.subgroups),
                        _reason_for(candidate, result.matched_term, hospital_count, language),
                        ft,
                        language,
                    )
                    for index, candidate in enumerate(result.candidates, start=1)
                ),
                _nearby_prompt(result, ft, language),
                *_source_section(references, ft, language),
            ],
        },
        "footer": _footer(ft, language),
    }


def build_symptom_department_flex(
    result: SymptomTriageResult,
    *,
    references: tuple[SourceReference, ...] | None = None,
    font_size: str | None = None,
    language: str | None = None,
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
    lang = normalize_user_language(language or get_request_language())
    return {
        "type": "flex",
        "altText": t("flex.symptom.alt", lang),
        "contents": _build_suggestion_bubble(result, resolved, ft, lang),
        "quickReply": _nearby_quick_reply(_nearby_departments(result), lang),
        SYMPTOM_DEPARTMENT_KEY: {
            "kind": result.kind,
            "departments": [c.canonical for c in result.candidates],
        },
    }
