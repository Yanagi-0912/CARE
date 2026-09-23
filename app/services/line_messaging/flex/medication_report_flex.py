"""聊天裡回報服藥的卡片：已記錄、已取消、請問是哪一頓。

**為什麼要是卡片而不是純文字**：這三張都要按鈕。

- 已記錄那張要掛「記錯了」——`record_medication_taken` 是模型判讀出來的，它把
  「我吃了沒」讀成「我吃了」時，那一頓會被標成 taken，T+20 催促與 T+30 家屬
  逾時警報就此靜音。按鈕是使用者自己按的，不必再經過模型一次。
- 反問那張直接把候選時段做成按鈕：使用者不必再打一次字，系統也不必再判讀
  一次「他說的是哪一頓」——挑錯就是把另一頓標成吃過了。

卡片經由 agent 的 `medical_tool_names` 原樣送出（見 agent.py），所以這裡回的是
可以直接 `json.dumps` 的 payload dict，不是 SDK 的 FlexMessage；dispatcher 那條
路（按下按鈕之後）則用 `as_flex_message` 包成 FlexMessage。
"""

from __future__ import annotations

from typing import Any, NamedTuple, Optional, Sequence

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.i18n import t
from app.services.line_messaging.flex.medication_flex import get_slot_display_name
from resources.flex_messages import theme
from resources.flex_messages.size_guard import fits

# postback action 名稱。與 `confirm_medication` 分開：那個是推播卡上的【我已用藥】，
# 這兩個是聊天回報專用，帶的參數與後續行為都不同。
UNDO_REPORT_ACTION = "undo_medication_report"
REPORT_SLOT_ACTION = "report_medication_slot"

# 反問時最多列幾顆按鈕。一天的時段上限是四個（早中晚睡前），列滿也不會爆版；
# 定成常數只是讓呼叫端不必自己記這件事。
MAX_SLOT_CHOICES = 4


class SlotChoice(NamedTuple):
    """反問卡上的一個候選時段。"""

    log_id: str
    slot_type: str
    scheduled_time: str


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
    slot_name: str, line: str, ft: theme.FlexTheme, background: str
) -> dict[str, Any]:
    """哪一頓、幾點服用——使用者要一眼確認「記到的是不是他講的那一頓」。"""
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": background,
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
                "text": line,
                "size": ft.body,
                "color": theme.BRAND_DARK,
                "wrap": True,
            },
        ],
    }


def _medication_lines(
    names: Sequence[str], ft: theme.FlexTheme
) -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": f"✓ {name}",
            "size": ft.body,
            "color": theme.TEXT,
            "wrap": True,
        }
        for name in names
    ]


def _paragraph(text: str, ft: theme.FlexTheme, color: str = theme.TEXT_MUTED) -> dict[str, Any]:
    return {"type": "text", "text": text, "size": ft.caption, "color": color, "wrap": True}


def _bubble(
    header_label: str,
    body_contents: list[dict[str, Any]],
    buttons: list[dict[str, Any]],
    ft: theme.FlexTheme,
    header_color: str = theme.BRAND,
) -> dict[str, Any]:
    bubble: dict[str, Any] = {
        "type": "bubble",
        "header": _header(header_label, ft, header_color),
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "xl",
            "spacing": "md",
            "contents": body_contents,
        },
    }
    if buttons:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "lg",
            "contents": buttons,
        }
    return bubble


def build_report_bubble(
    *,
    log_id: str,
    slot_type: str,
    scheduled_time: str,
    taken_time: str,
    medication_names: Sequence[str],
    ft: theme.FlexTheme,
    language: Optional[str] = None,
    reverted: bool = False,
) -> dict[str, Any]:
    """已記錄／已取消的卡片。

    `reverted=True` 是按下「記錯了」之後的樣子：同一張卡、換掉標題與說明、
    拿掉按鈕。不換成一則純文字，是為了讓使用者在對話裡看到的是「同一件事的
    後續狀態」，而不是兩則看起來無關的訊息。
    """
    slot_name = get_slot_display_name(slot_type, language)
    if reverted:
        header_label = t("flex.medreport.header.reverted", language)
        line = t("flex.medreport.back_to_unconfirmed", language)
        background = theme.NEUTRAL_BG
    else:
        header_label = t("flex.medreport.header.done", language)
        line = t("flex.medreport.taken_at", language).format(time=taken_time)
        background = theme.BRAND_TINT

    body_contents: list[dict[str, Any]] = [
        _slot_block(f"{slot_name} {scheduled_time}", line, ft, background)
    ]
    if medication_names and not reverted:
        body_contents.extend(_medication_lines(medication_names, ft))
    body_contents.append(
        _paragraph(
            t("flex.medreport.hint.reverted" if reverted else "flex.medreport.hint.done", language),
            ft,
        )
    )

    buttons: list[dict[str, Any]] = []
    if not reverted:
        undo_label = t("flex.medreport.button.undo", language)
        buttons.append(
            ft.secondary_button(
                undo_label,
                {
                    "type": "postback",
                    "label": undo_label,
                    "data": f"action={UNDO_REPORT_ACTION}&log_id={log_id}",
                    "displayText": t("flex.medreport.display.undo", language),
                },
            )
        )
    return _bubble(header_label, body_contents, buttons, ft)


def build_slot_choice_bubble(
    *,
    choices: Sequence[SlotChoice],
    taken_time: str,
    ft: theme.FlexTheme,
    language: Optional[str] = None,
) -> dict[str, Any]:
    """「請問是哪一頓？」——候選時段做成按鈕，使用者按一下就記下去。"""
    body_contents = [_paragraph(t("flex.medreport.which_slot", language), ft, theme.TEXT)]
    buttons = []
    for choice in list(choices)[:MAX_SLOT_CHOICES]:
        label = f"{get_slot_display_name(choice.slot_type, language)} {choice.scheduled_time}"
        buttons.append(
            ft.primary_button(
                label,
                {
                    "type": "postback",
                    "label": label,
                    # 使用者說的服藥時刻跟著按鈕走：他在反問之前就講了「12 點吃的」，
                    # 按下按鈕的現在（可能又過了幾分鐘）不是那個事實。
                    "data": f"action={REPORT_SLOT_ACTION}&log_id={choice.log_id}&at={taken_time}",
                    "displayText": t("flex.medreport.display.slot", language).format(
                        slot=label
                    ),
                },
            )
        )
    return _bubble(
        t("flex.medreport.header.which", language), body_contents, buttons, ft
    )


def as_payload(bubble: dict[str, Any], alt_text: str, speech_text: str = "") -> Optional[dict]:
    """包成 agent 工具回傳用的 Flex payload；超過大小上限時回 None 讓呼叫端退回純文字。"""
    if not fits(bubble):
        return None
    payload: dict[str, Any] = {"type": "flex", "altText": alt_text, "contents": bubble}
    if speech_text:
        payload["speechText"] = speech_text
    return payload


def as_flex_message(bubble: dict[str, Any], alt_text: str) -> FlexMessage:
    """包成 SDK 的 FlexMessage，供 dispatcher（按下按鈕之後）回覆用。"""
    return FlexMessage(altText=alt_text, contents=FlexContainer.from_dict(bubble))
