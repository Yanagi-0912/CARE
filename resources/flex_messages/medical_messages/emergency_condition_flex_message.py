"""
緊急狀況卡片：急迫度判斷為 emergency 時送出的 Flex Message，附可直接撥號的按鈕。
號碼（119／110）是台灣的固定值不翻譯，但單位名稱要翻，否則使用者不知道打過去是誰接。

字級：
    文字大小走 theme.resolve_theme()，跟隨 UserSettings.font_size。這張卡的
    使用者多半處於不好操作手機的狀態，字級設定在這裡比在任何一張卡都重要。


版面對齊 emergency_condition_flex_template.json（模板以預設語言與 large 字級
產生），由 test_emergency_condition_flex.py 逐節點比對。
"""

from __future__ import annotations

from typing import Any

from app.i18n.messages import t
from app.services.medical.symptom_classification.urgency import UrgencyVerdict
from resources.flex_messages import theme

# --- 模板樣式常數。改這裡等同改模板，兩邊必須同步（有測試比對）---------------
_TPL_HEADER_BG = "#C62828"
_TPL_BUTTON_BG = "#B71C1C"
_TPL_ON_DARK = "#FFFFFF"
_TPL_ON_DARK_MUTED = "#EEEEEE"

_BODY_KEYS: tuple[str, ...] = (
    "emergency.body.1",
    "emergency.body.2",
    "emergency.body.3",
)


def alt_text(language: str | None = None) -> str:
    """LINE 通知列與不支援 Flex 的裝置看到的文字，同樣要跟著語言走。"""
    return t("emergency.alt_text", language)


def _hotline_name(hotline, language: str | None) -> str:
    """
    專線名稱以號碼當 key 查翻譯。查不到就退回 Hotline.name 的原文——看得懂
    中文總比看到一個 i18n key 好（沿用 department_label 的降級原則）。
    """
    key = f"emergency.hotline.{hotline.number}"
    translated = t(key, language)
    return hotline.name if translated == key else translated


def _hotline_note(hotline, language: str | None) -> str:
    key = f"emergency.hotline.{hotline.number}.note"
    translated = t(key, language)
    return hotline.note if translated == key else translated


def _text(
    value: str,
    *,
    size: str,
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


def _call_button(
    hotline, *, primary: bool, ft: theme.FlexTheme, language: str | None
) -> dict[str, Any]:
    """
    撥號按鈕。主按鈕實心、次要按鈕淺底加深色字，兩者都保留足夠的點擊面積——
    這張卡的使用者多半處於不好操作手機的狀態。
    """
    label = t("emergency.call_button", language).format(
        name=_hotline_name(hotline, language), number=hotline.number
    )
    note = _hotline_note(hotline, language)
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
                size=ft.button,
                weight="bold",
                color=_TPL_ON_DARK if primary else _TPL_BUTTON_BG,
                align="center",
            ),
            *(
                [
                    _text(
                        note,
                        size=ft.caption,
                        color=_TPL_ON_DARK_MUTED if primary else theme.TEXT_MUTED,
                        align="center",
                        margin="xs",
                    )
                ]
                if note
                else []
            ),
        ],
    }


def _header(display: str, *, ft: theme.FlexTheme, language: str | None) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": _TPL_HEADER_BG,
        "paddingAll": "20px",
        "contents": [
            _text(
                t("emergency.headline", language),
                size=ft.heading,
                color=_TPL_ON_DARK,
                weight="bold",
            ),
            _text(display, size=ft.caption, color=_TPL_ON_DARK_MUTED, margin="md"),
        ],
    }


def build_emergency_condition_flex(
    verdict: UrgencyVerdict,
    *,
    language: str | None = None,
    font_size: str | None = None,
) -> dict[str, Any]:
    """把急迫度判斷組成可直接送往 LINE 的 Flex Message 外層結構。

    language／font_size 省略時讀 request-scoped 的 ContextVar（webhook 進來時由
    handler 依使用者設定寫入），所以正常流程不必層層傳參；留著參數是為了讓測試
    與其他呼叫端能明確指定。
    """
    ft = theme.resolve_theme(font_size)

    body_contents: list[dict[str, Any]] = [
        _text(t(key, language), size=ft.body) for key in _BODY_KEYS
    ]

    if verdict.hotlines:
        body_contents.append({"type": "separator", "margin": "xl"})
        body_contents.append(
            _text(
                t("emergency.hotline_label", language),
                size=ft.caption,
                color=theme.TEXT_MUTED,
                margin="xl",
            )
        )
        for index, hotline in enumerate(verdict.hotlines):
            body_contents.append(
                _call_button(
                    hotline, primary=index == 0, ft=ft, language=language
                )
            )

    display = verdict.display or t("emergency.default_display", language)
    bubble = {
        "type": "bubble",
        "size": "mega",
        "header": _header(display, ft=ft, language=language),
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
            "contents": [
                _text(
                    t("emergency.footer", language),
                    size=ft.caption,
                    color=theme.TEXT_FAINT,
                )
            ],
        },
    }

    return {
        "type": "flex",
        "altText": alt_text(language),
        "contents": bubble,
    }
