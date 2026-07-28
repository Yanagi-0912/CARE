import logging
from typing import Optional
from linebot.v3.messaging import FlexContainer, FlexMessage

from app.models.medication import SLOT_DISPLAY_NAMES

logger = logging.getLogger(__name__)


def get_slot_display_name(slot_type: str) -> str:
    return SLOT_DISPLAY_NAMES.get(slot_type, slot_type)


def build_patient_medication_flex(
    log_id: str,
    slot_type: str,
    scheduled_time: str,
    disabled: bool = False,
    taken_at_str: Optional[str] = None,
) -> FlexMessage:
    """
    建立傳送給用藥者的服藥提醒 Flex Message。
    - disabled=False: 顯示【我已用藥】可點擊按鈕
    - disabled=True: 顯示【已完成用藥】灰色停用按鈕 (點擊後動態替換)
    """
    slot_name = get_slot_display_name(slot_type)

    if not disabled:
        alt_text = f"CARE 用藥提醒：【{slot_name}】服藥時間到了"
        header_color = "#1DB446"
        header_title = "CARE 用藥提醒"
        body_text = f"時段：【{slot_name}】服藥時間 ({scheduled_time})"
        sub_text = "請於 30 分鐘內服藥並點擊下方按鈕確認。"
        footer_button = {
            "type": "button",
            "action": {
                "type": "postback",
                "label": "我已用藥",
                "data": f"action=confirm_medication&log_id={log_id}",
                "displayText": "我已經完成用藥了",
            },
            "style": "primary",
            "color": "#1DB446",
        }
    else:
        time_display = f"於 {taken_at_str} " if taken_at_str else ""
        alt_text = f"已完成【{slot_name}】用藥"
        header_color = "#757575"
        header_title = "用藥已完成紀錄"
        body_text = f"時段：【{slot_name}】服藥時間 ({scheduled_time})"
        sub_text = f"感謝您的紀錄，服藥紀錄已成功登錄！"
        footer_button = {
            "type": "button",
            "action": {
                "type": "postback",
                "label": f"已{time_display}完成用藥",
                "data": "action=already_done",
            },
            "style": "secondary",
            "color": "#CCCCCC",
            "disabled": True,
        }

    bubble_dict = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": header_color,
            "contents": [
                {
                    "type": "text",
                    "text": header_title,
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "md",
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": body_text,
                    "weight": "bold",
                    "size": "lg",
                    "color": "#111111",
                },
                {
                    "type": "text",
                    "text": sub_text,
                    "size": "sm",
                    "color": "#666666",
                    "margin": "xs",
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [footer_button],
        },
    }

    container = FlexContainer.from_dict(bubble_dict)
    return FlexMessage(altText=alt_text, contents=container)


def build_patient_urgent_reminder_flex(
    log_id: str, slot_type: str, scheduled_time: str
) -> FlexMessage:
    """
    T+20min 傳送給用藥者的二次溫馨催促 Flex Message 
    """
    slot_name = get_slot_display_name(slot_type)
    alt_text = f"CARE 溫馨催促：您尚未完成【{slot_name}】服藥點擊！"

    bubble_dict = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1DB446",
            "contents": [
                {
                    "type": "text",
                    "text": "CARE 溫馨催促提醒",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "md",
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": f"【{slot_name}】服藥時間 ({scheduled_time})",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#111111",
                },
                {
                    "type": "text",
                    "text": "您尚未點擊【我已用藥】按鈕。請即刻服藥並點擊下方按鈕確認喔！",
                    "size": "sm",
                    "color": "#666666",
                    "wrap": True,
                    "margin": "xs",
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "我已用藥",
                        "data": f"action=confirm_medication&log_id={log_id}",
                        "displayText": "我已經完成用藥了",
                    },
                    "style": "primary",
                    "color": "#1DB446",
                }
            ],
        },
    }

    container = FlexContainer.from_dict(bubble_dict)
    return FlexMessage(altText=alt_text, contents=container)


def build_caregiver_alert_flex(
    patient_name: str, slot_type: str, scheduled_time: str
) -> FlexMessage:
    """
    T+30min 傳送給通報對象家屬的逾時未用藥關心 Flex Message (保持統一綠色主題 #1DB446)
    """
    slot_name = get_slot_display_name(slot_type)
    alt_text = f"關心提醒：成員【{patient_name}】逾時未服藥"

    bubble_dict = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1DB446",
            "contents": [
                {
                    "type": "text",
                    "text": "關心提醒：成員逾時未服藥",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "md",
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": f"成員 【{patient_name}】 預定於 {scheduled_time} 服用【{slot_name}】藥物，截至目前（逾時 30 分鐘）仍未點擊完成用藥。",
                    "size": "sm",
                    "wrap": True,
                    "color": "#333333",
                },
                {
                    "type": "text",
                    "text": "請您抽空給予關心或撥打電話關懷家庭成員！",
                    "size": "sm",
                    "color": "#333333",
                    "weight": "bold",
                    "margin": "md",
                    "wrap": True,
                },
            ],
        },
    }

    container = FlexContainer.from_dict(bubble_dict)
    return FlexMessage(altText=alt_text, contents=container)
