import logging
from typing import Any, Optional

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.i18n import t
from app.models.medication import SLOT_DISPLAY_NAMES
from resources.flex_messages import theme

logger = logging.getLogger(__name__)


def get_slot_display_name(slot_type: str, language: str | None = None) -> str:
    """取得時段的在地化名稱；未知時段回退為原始值。"""
    if slot_type not in SLOT_DISPLAY_NAMES:
        return slot_type
    return t(f"slot.{slot_type}", language)


def _header(label: str, ft: theme.FlexTheme, background: str = theme.BRAND) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": background,
        "paddingAll": "lg",
        "contents": [
            {
                "type": "text",
                "text": label,
                "color": theme.TEXT_ON_BRAND,
                "weight": "bold",
                "size": ft.heading,
                "wrap": True,
            }
        ],
    }


def _slot_block(
    slot_name: str, scheduled_time: str, ft: theme.FlexTheme, language: str | None
) -> dict[str, Any]:
    """時段與時間的重點區塊，是使用者最需要一眼看到的資訊。"""
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": theme.BRAND_TINT,
        "cornerRadius": "md",
        "paddingAll": "lg",
        "spacing": "xs",
        "contents": [
            {
                "type": "text",
                "text": slot_name,
                "weight": "bold",
                "size": ft.title,
                "color": theme.BRAND_DARK,
                "wrap": True,
            },
            {
                "type": "text",
                "text": t("flex.med.scheduled_at", language).format(
                    time=scheduled_time
                ),
                "size": ft.body,
                "color": theme.BRAND_DARK,
                "wrap": True,
            },
        ],
    }


def _body(contents: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "xl",
        "backgroundColor": theme.SURFACE,
        "spacing": "md",
        "contents": contents,
    }


def _footer(button: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "lg",
        "contents": [button],
    }


def _paragraph(text: str, ft: theme.FlexTheme, color: str = theme.TEXT_MUTED, **extra) -> dict[str, Any]:
    return {
        "type": "text",
        "text": text,
        "size": ft.body,
        "color": color,
        "wrap": True,
        **extra,
    }


def build_patient_medication_flex(
    log_id: str,
    slot_type: str,
    scheduled_time: str,
    disabled: bool = False,
    taken_at_str: Optional[str] = None,
    language: str | None = None,
    font_size: str | None = None,
) -> FlexMessage:
    """
    建立傳送給用藥者的服藥提醒 Flex Message。
    - disabled=False: 顯示【我已用藥】可點擊按鈕
    - disabled=True: 顯示已完成的停用狀態 (點擊後動態替換)
    """
    ft = theme.resolve_theme(font_size)
    slot_name = get_slot_display_name(slot_type, language)

    if not disabled:
        alt_text = t("flex.med.alt.reminder", language).format(slot=slot_name)
        taken_label = t("flex.med.button.taken", language)
        bubble_dict = {
            "type": "bubble",
            "header": _header(t("flex.med.header.reminder", language), ft),
            "body": _body(
                [
                    _slot_block(slot_name, scheduled_time, ft, language),
                    _paragraph(
                        t("flex.med.instruction", language), ft, margin="md"
                    ),
                ]
            ),
            "footer": _footer(
                ft.primary_button(
                    taken_label,
                    {
                        "type": "postback",
                        "label": taken_label,
                        "data": f"action=confirm_medication&log_id={log_id}",
                        "displayText": t("flex.med.display.taken", language),
                    },
                )
            ),
        }
    else:
        alt_text = t("flex.med.alt.done", language).format(slot=slot_name)
        completion_text = (
            t("flex.med.done_at", language).format(time=taken_at_str)
            if taken_at_str
            else t("flex.med.done", language)
        )
        bubble_dict = {
            "type": "bubble",
            "header": _header(
                t("flex.med.header.done", language), ft, background=theme.STATUS_UNKNOWN
            ),
            "body": _body(
                [
                    _slot_block(slot_name, scheduled_time, ft, language),
                    _paragraph(t("flex.med.thanks", language), ft, margin="md"),
                ]
            ),
            "footer": _footer(
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": theme.NEUTRAL_BG,
                    "cornerRadius": "md",
                    "paddingAll": "lg",
                    "contents": [
                        {
                            "type": "text",
                            "text": completion_text,
                            "color": theme.TEXT_FAINT,
                            "weight": "bold",
                            "size": ft.button,
                            "align": "center",
                            "wrap": True,
                        }
                    ],
                }
            ),
        }

    container = FlexContainer.from_dict(bubble_dict)
    return FlexMessage(altText=alt_text, contents=container)


def build_patient_urgent_reminder_flex(
    log_id: str,
    slot_type: str,
    scheduled_time: str,
    language: str | None = None,
    font_size: str | None = None,
) -> FlexMessage:
    """T+20min 傳送給用藥者的二次催促 Flex Message"""
    ft = theme.resolve_theme(font_size)
    slot_name = get_slot_display_name(slot_type, language)
    taken_label = t("flex.med.button.taken", language)

    bubble_dict = {
        "type": "bubble",
        "header": _header(
            t("flex.med.header.urgent", language), ft, background=theme.STATUS_CLOSED
        ),
        "body": _body(
            [
                _slot_block(slot_name, scheduled_time, ft, language),
                _paragraph(t("flex.med.urgent_body", language), ft, margin="md"),
            ]
        ),
        "footer": _footer(
            ft.primary_button(
                taken_label,
                {
                    "type": "postback",
                    "label": taken_label,
                    "data": f"action=confirm_medication&log_id={log_id}",
                    "displayText": t("flex.med.display.taken", language),
                },
            )
        ),
    }

    container = FlexContainer.from_dict(bubble_dict)
    return FlexMessage(
        altText=t("flex.med.alt.urgent", language).format(slot=slot_name),
        contents=container,
    )


def build_caregiver_alert_flex(
    patient_name: str,
    slot_type: str,
    scheduled_time: str,
    language: str | None = None,
    font_size: str | None = None,
) -> FlexMessage:
    """T+30min 傳送給通報對象家屬的逾時未用藥關心 Flex Message"""
    ft = theme.resolve_theme(font_size)
    slot_name = get_slot_display_name(slot_type, language)

    bubble_dict = {
        "type": "bubble",
        "header": _header(t("flex.med.header.caregiver", language), ft),
        "body": _body(
            [
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
                            "text": f"{slot_name}　{scheduled_time}",
                            "size": ft.body,
                            "color": theme.TEXT_MUTED,
                            "wrap": True,
                        },
                    ],
                },
                _paragraph(
                    t("flex.med.overdue", language),
                    ft,
                    color=theme.STATUS_CLOSED,
                    weight="bold",
                    margin="md",
                ),
                _paragraph(t("flex.med.please_care", language), ft),
            ]
        ),
    }

    container = FlexContainer.from_dict(bubble_dict)
    return FlexMessage(
        altText=t("flex.med.alt.caregiver", language).format(name=patient_name),
        contents=container,
    )
