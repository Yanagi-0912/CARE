"""經期異常的推播卡（health-alerts spec「經期異常只通知本人」）。

文字刻意通用，**SHALL NOT** 含「經期」「月經」（或其他五語系的對應詞）或
任何數值，只提示「有一筆健康紀錄需要留意」並提供開啟 LIFF 的方式——LINE
推播會出現在手機鎖定畫面的預覽中，長輩常與家人共用手機（spec 原文的理由）。

只送本人，因此不像 ``health_alert_flex`` 需要 ``patient_name``／
``recorder_name`` 這類「這是誰的紀錄」的資訊。
"""

from __future__ import annotations

from typing import Any, Optional

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.i18n import t
from resources.flex_messages import theme


def menstrual_alert_alt_text(language: Optional[str] = None) -> str:
    return t("flex.menstrual_alert.alt", language)


def build_menstrual_alert_flex(
    *,
    button_uri: str = "",
    language: Optional[str] = None,
    font_size: Optional[str] = None,
) -> FlexMessage:
    """經期異常卡。``button_uri`` 省略時不顯示按鈕（同 ``health_alert_flex``
    的慣例：呼叫端在 ``LIFF_URL`` 未設定時傳空字串）。
    """
    ft = theme.resolve_theme(font_size)
    bubble: dict[str, Any] = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": theme.BRAND,
            "paddingAll": "lg",
            "contents": [
                {
                    "type": "text",
                    "text": t("flex.menstrual_alert.header", language),
                    "color": theme.TEXT_ON_BRAND,
                    "weight": "bold",
                    "size": ft.heading,
                    "wrap": True,
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "xl",
            "backgroundColor": theme.SURFACE,
            "contents": [
                {
                    "type": "text",
                    "text": t("flex.menstrual_alert.body", language),
                    "size": ft.body,
                    "color": theme.TEXT,
                    "wrap": True,
                }
            ],
        },
    }

    if button_uri:
        label = t("flex.menstrual_alert.button", language)
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "lg",
            "contents": [
                ft.primary_button(label, {"type": "uri", "label": label, "uri": button_uri})
            ],
        }

    return FlexMessage(
        altText=menstrual_alert_alt_text(language),
        contents=FlexContainer.from_dict(bubble),
    )
