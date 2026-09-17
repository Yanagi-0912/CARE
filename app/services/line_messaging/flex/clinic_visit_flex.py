"""看診錄音整理完成（或失敗）的 Flex 卡片。

**隱私邊界比照掛號提醒：推播只含醫院名稱。** 摘要、用藥變動、原文一律只在 LIFF 內
顯示——LINE 訊息會躺在聊天室列表裡，同一支手機的其他人也看得到，而診間對話比科別
還敏感。所以這裡的 builder 參數**根本沒有**摘要欄位，不是靠呼叫端記得不傳。

版面元件沿用用藥提醒（medication_flex）的 header／body／paragraph，與掛號提醒同一套。
"""

from typing import Any, Optional

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.i18n import t
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


def build_clinic_visit_flex(
    *,
    header: str,
    body_text: str,
    hospital_name: str,
    open_url: Optional[str],
    language: Optional[str] = None,
    font_size: Optional[str] = None,
) -> FlexMessage:
    """`open_url` 為 None（沒設 LIFF_URL）時不放按鈕，卡片仍然送得出去。"""
    ft = theme.resolve_theme(font_size)
    contents: list[dict[str, Any]] = []
    if hospital_name:
        contents.append(
            _paragraph(hospital_name, ft, color=theme.BRAND_DARK, weight="bold")
        )
    contents.append(_paragraph(body_text, ft))

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
