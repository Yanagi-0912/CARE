"""用藥風險通報給家人的卡片。

內容只有三樣：當事人姓名、藥品名稱、風險類型說明。SHALL NOT 帶入使用者的原始
提問或圖片的 OCR 全文——原話常帶病情（「我最近睡不著想吃這個」），藥袋全文同時
帶病患姓名與就診機構，而推播會出現在通知列與鎖定畫面，可能被非預期的人看到。
與 `Medication.indication` 不進推播是同一條理由。
"""

from typing import Any

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.i18n import t
from resources.flex_messages import theme


def build_family_alert_flex(
    patient_name: str,
    drug_name: str,
    risk_reason: str,
    language: str | None = None,
    font_size: str | None = None,
) -> FlexMessage:
    """高風險通報卡片。語言與字級取的是**收件家人本人**的設定，不是當事人的。"""
    ft = theme.resolve_theme(font_size)

    body_contents: list[dict[str, Any]] = [
        {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": theme.SURFACE_ALT,
            "cornerRadius": "md",
            "paddingAll": "lg",
            "spacing": "xs",
            "contents": [
                {
                    "type": "text",
                    "text": patient_name,
                    "weight": "bold",
                    "size": ft.title,
                    "color": theme.TEXT,
                    "wrap": True,
                },
                {
                    "type": "text",
                    "text": t("flex.safety.family.intro", language),
                    "size": ft.body,
                    "color": theme.TEXT_MUTED,
                    "wrap": True,
                },
                {
                    "type": "text",
                    "text": drug_name,
                    "weight": "bold",
                    "size": ft.body,
                    "color": theme.TEXT,
                    "wrap": True,
                    "margin": "sm",
                },
            ],
        },
        {
            "type": "text",
            "text": risk_reason,
            "size": ft.body,
            "color": theme.STATUS_CLOSED,
            "weight": "bold",
            "wrap": True,
            "margin": "md",
        },
        {
            "type": "text",
            "text": t("flex.safety.family.please_check", language),
            "size": ft.body,
            "color": theme.TEXT_MUTED,
            "wrap": True,
        },
    ]

    bubble_dict = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": theme.BRAND,
            "paddingAll": "lg",
            "contents": [
                {
                    "type": "text",
                    "text": t("flex.safety.header.family", language),
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
            "spacing": "md",
            "contents": body_contents,
        },
    }

    return FlexMessage(
        altText=t("flex.safety.alt.family", language).format(name=patient_name),
        contents=FlexContainer.from_dict(bubble_dict),
    )
