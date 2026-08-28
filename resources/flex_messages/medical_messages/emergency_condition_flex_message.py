"""
緊急狀況卡片：急迫度判斷為 emergency 時送出的 Flex Message，附可直接撥號的按鈕。

為什麼這張卡 SHALL NOT 出現任何門診科別：
    並陳「可能是內科，但也請留意是否需要急診」等於把判斷責任推回給正在不舒服
    的人，實質上就是導向門診。這條性質有測試守著。

撥號按鈕：
    action 為 uri、uri 為 tel:數字。LINE 會在點擊時開啟撥號介面。號碼由
    Hotline.tel_uri 產生，只留數字——全形字元或分隔符會讓某些裝置撥不出去。

版面對齊 emergency_condition_flex_template.json，樣式常數集中在 _TPL_*，
由 test_emergency_condition_flex.py 直接對模板逐節點比對。
"""

from __future__ import annotations

from typing import Any

from app.services.medical.symptom_classification.urgency import UrgencyVerdict
from resources.flex_messages import theme

ALT_TEXT_EMERGENCY = "請立即就醫"

# --- 模板樣式常數。改這裡等同改模板，兩邊必須同步（有測試比對）---------------
_TPL_HEADER_BG = "#C62828"
_TPL_BUTTON_BG = "#B71C1C"
_TPL_ON_DARK = "#FFFFFF"
_TPL_ON_DARK_MUTED = "#EEEEEE"

_HEADLINE = "請立即就醫"
_DEFAULT_DISPLAY = "你描述的狀況可能需要立即處置"

_BODY_LINES: tuple[str, ...] = (
    "你描述的狀況可能需要緊急處置，不建議等待一般門診掛號。",
    "請儘快前往最近的急診，或撥打 119 請求協助。",
    "若身邊有人，請讓對方陪同前往。",
)

_HOTLINE_LABEL = "可以馬上撥打"
_FOOTER_TEXT = "本訊息不是醫療診斷。情況緊急時請以撥打 119 或前往急診為優先。"


def _text(
    value: str,
    *,
    size: str = "md",
    color: str = theme.TEXT,
    weight: str | None = None,
    margin: str | None = None,
    align: str | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "text",
        "text": value,
        "size": size,
        "color": color,
        "wrap": True,
    }
    if weight:
        node["weight"] = weight
    if margin:
        node["margin"] = margin
    if align:
        node["align"] = align
    return node


def _call_button(hotline, *, primary: bool) -> dict[str, Any]:
    """
    撥號按鈕。主按鈕實心、次要按鈕淺底加深色字，兩者都保留足夠的點擊面積——
    這張卡的使用者多半處於不好操作手機的狀態。
    """
    label = f"撥打 {hotline.name} {hotline.number}"
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": _TPL_BUTTON_BG if primary else theme.SURFACE,
        "borderColor": _TPL_BUTTON_BG,
        "borderWidth": "1px",
        "cornerRadius": "md",
        "paddingAll": "lg",
        "margin": "md",
        "action": {"type": "uri", "label": label, "uri": hotline.tel_uri},
        "contents": [
            _text(
                label,
                size="lg",
                weight="bold",
                color=_TPL_ON_DARK if primary else _TPL_BUTTON_BG,
                align="center",
            ),
            *(
                [
                    _text(
                        hotline.note,
                        size="xs",
                        color=_TPL_ON_DARK_MUTED if primary else theme.TEXT_MUTED,
                        align="center",
                        margin="xs",
                    )
                ]
                if hotline.note
                else []
            ),
        ],
    }


def _header(display: str) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": _TPL_HEADER_BG,
        "paddingAll": "20px",
        "contents": [
            _text(_HEADLINE, size="xxl", color=_TPL_ON_DARK, weight="bold"),
            _text(display, size="sm", color=_TPL_ON_DARK_MUTED, margin="md"),
        ],
    }


def build_emergency_condition_flex(verdict: UrgencyVerdict) -> dict[str, Any]:
    """把急迫度判斷組成可直接送往 LINE 的 Flex Message 外層結構。"""
    body_contents: list[dict[str, Any]] = [_text(line) for line in _BODY_LINES]

    if verdict.hotlines:
        body_contents.append({"type": "separator", "margin": "xl"})
        body_contents.append(
            _text(_HOTLINE_LABEL, size="sm", color=theme.TEXT_MUTED, margin="xl")
        )
        for index, hotline in enumerate(verdict.hotlines):
            body_contents.append(_call_button(hotline, primary=index == 0))

    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": _header(verdict.display or _DEFAULT_DISPLAY),
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "20px",
            "spacing": "md",
            "backgroundColor": theme.SURFACE,
            "contents": body_contents,
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "backgroundColor": theme.SURFACE_ALT,
            "contents": [_text(_FOOTER_TEXT, size="xs", color=theme.TEXT_FAINT)],
        },
    }

    return {"type": "flex", "altText": ALT_TEXT_EMERGENCY, "contents": bubble}
