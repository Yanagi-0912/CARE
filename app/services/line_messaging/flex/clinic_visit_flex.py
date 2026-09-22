"""看診錄音整理完成（或失敗）的 Flex 卡片。

**2026-09-22 起卡片直接放摘要，本人與家人都一樣**（James 決定）。原本只放醫院名稱、
內容一律在 LIFF 看，理由是 LINE 訊息會躺在聊天室列表裡、同一支手機的其他人也看得到；
錄音改在聊天室之後，看結果還要開 LIFF 等於沒搬完，他選了方便。收件人規則沒有變
（見 notifier）：看得到這份紀錄的人才收得到。

逐字稿太長不放，按鈕開完整原文（有 LIFF_URL 時）。

版面元件沿用用藥提醒（medication_flex）的 header／body／paragraph，與掛號提醒同一套。
"""

from typing import Any, Optional

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.i18n import t
from app.models.clinic_transcript import ClinicVisitSummaryModel
from app.services.line_messaging.flex.medication_flex import _body, _header, _paragraph
from resources.flex_messages import theme

# LINE 的上限：altText 400 字、按鈕 label 20 字。
_ALT_TEXT_MAX = 400
_LABEL_MAX = 20


def _footer(button: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "lg",
        "contents": [button],
    }


def summary_sections(
    summary: ClinicVisitSummaryModel, language: Optional[str]
) -> list[tuple[str, list[str]]]:
    """(小標, 各行) 依卡片上的順序；純文字備援也用同一份。空的欄位不列。"""
    sections: list[tuple[str, list[str]]] = []
    if summary.main_points:
        sections.append((t("flex.clinic.section.main_points", language), list(summary.main_points)))
    if summary.medication_changes:
        lines = []
        for change in summary.medication_changes:
            lines.append(change.description)
            if change.quote:
                lines.append(t("flex.clinic.quote", language).format(quote=change.quote))
        sections.append((t("flex.clinic.section.medication_changes", language), lines))
    if summary.next_visit:
        sections.append((t("flex.clinic.section.next_visit", language), [summary.next_visit]))
    if summary.reminders:
        sections.append((t("flex.clinic.section.reminders", language), list(summary.reminders)))
    if summary.unclear:
        sections.append((t("flex.clinic.section.unclear", language), list(summary.unclear)))
    return sections


def build_clinic_visit_flex(
    *,
    header: str,
    body_text: str,
    hospital_name: str,
    open_url: Optional[str],
    language: Optional[str] = None,
    font_size: Optional[str] = None,
    summary: Optional[ClinicVisitSummaryModel] = None,
    self_recap: bool = False,
) -> FlexMessage:
    """`summary` 為 None 是失敗卡；`open_url` 為 None（沒設 LIFF_URL）時不放按鈕。"""
    ft = theme.resolve_theme(font_size)
    contents: list[dict[str, Any]] = []
    if hospital_name:
        contents.append(
            _paragraph(hospital_name, ft, color=theme.BRAND_DARK, weight="bold")
        )
    contents.append(_paragraph(body_text, ft))
    if self_recap:
        contents.append(_paragraph(t("flex.clinic.self_recap_note", language), ft, color=theme.TEXT_FAINT))

    if summary is not None:
        sections = summary_sections(summary, language)
        if not sections:
            contents.append(_paragraph(t("flex.clinic.empty_summary", language), ft))
        for title, lines in sections:
            contents.append(_paragraph(title, ft, color=theme.BRAND_DARK, weight="bold"))
            contents.extend(_paragraph(f"・{line}", ft) for line in lines)

    bubble: dict[str, Any] = {
        "type": "bubble",
        "header": _header(header, ft),
        "body": _body(contents),
    }
    if open_url:
        label = t("flex.clinic.button.open", language)
        bubble["footer"] = _footer(
            ft.primary_button(
                label,
                {"type": "uri", "label": label[:_LABEL_MAX], "uri": open_url},
            )
        )
    alt = f"{header}：{hospital_name}" if hospital_name else header
    return FlexMessage(altText=alt[:_ALT_TEXT_MAX], contents=FlexContainer.from_dict(bubble))
