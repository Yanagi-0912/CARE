"""掛號提醒的 Flex 卡片。

**隱私邊界（已拍板）：推播只含日期時間與醫院名稱。** 科別、醫師、看診號、備註一律
只在 LIFF 內顯示——這裡每一支 builder 的參數**根本沒有**那些欄位，不是靠呼叫端
記得不傳。家屬每一次門診都會收到最多三則，科別若進文案，等於每次看診都對全家廣播
一次科別；而 LINE 訊息會躺在聊天室列表裡，同一支手機的其他人也看得到。

版面元件沿用用藥提醒（medication_flex）的 header／body／paragraph，兩種提醒在同一個
聊天室裡看起來是同一個系統。

卡片按鈕的 postback 是 `action=appointment_depart|appointment_attend&appointment_id=…`，
由 LineEventDispatcher 交給 AppointmentService——與 LIFF 的 POST 端點走同一條路。
"""

from datetime import datetime
from typing import Any, Literal, Optional

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.i18n import t
from app.services.clinic_transcript.line_flow import START_ACTION as CLINIC_START_ACTION
from app.services.clinic_transcript.line_flow import postback_data as clinic_postback_data
from app.services.line_messaging.flex.medication_flex import _body, _header, _paragraph
from resources.flex_messages import theme

DEPART_ACTION = "appointment_depart"
ATTEND_ACTION = "appointment_attend"

_WEEKDAY_KEYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

# LINE 的上限：altText 400 字、postback label 20 字。
_ALT_TEXT_MAX = 400
_POSTBACK_LABEL_MAX = 20


def format_when(local_dt: datetime, language: Optional[str] = None) -> str:
    """門診時間的顯示字串，例如「9/15（週二）09:30」。

    `local_dt` 必須已經換到提醒自己的 offset（`AppointmentReminder.local_appointment_at`）
    ——這裡不做任何時區換算，收到幾點就寫幾點。
    """
    weekday = t(f"weekday.{_WEEKDAY_KEYS[local_dt.weekday()]}", language)
    return t("flex.appt.when", language).format(
        month=local_dt.month,
        day=local_dt.day,
        weekday=weekday,
        time=local_dt.strftime("%H:%M"),
    )


def format_hm(local_dt: datetime) -> str:
    return local_dt.strftime("%H:%M")


def _appointment_block(
    when_text: str,
    hospital_name: str,
    ft: theme.FlexTheme,
    patient_name: Optional[str],
    language: Optional[str],
) -> dict[str, Any]:
    """時間與醫院的重點區塊。家屬收到的版本多一行「誰的門診」。"""
    contents: list[dict[str, Any]] = []
    if patient_name:
        contents.append(
            {
                "type": "text",
                "text": t("flex.appt.patient_label", language).format(name=patient_name),
                "weight": "bold",
                "size": ft.body,
                "color": theme.BRAND_DARK,
                "wrap": True,
            }
        )
    contents.append(
        {
            "type": "text",
            "text": when_text,
            "weight": "bold",
            "size": ft.title,
            "color": theme.BRAND_DARK,
            "wrap": True,
        }
    )
    contents.append(
        {
            "type": "text",
            "text": hospital_name,
            "size": ft.body,
            "color": theme.BRAND_DARK,
            "wrap": True,
        }
    )
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": theme.BRAND_TINT,
        "cornerRadius": "md",
        "paddingAll": "lg",
        "spacing": "xs",
        "contents": contents,
    }


def _report_button(
    ft: theme.FlexTheme,
    action: str,
    reminder_id: str,
    label_key: str,
    language: Optional[str],
    *,
    primary: bool = True,
) -> dict[str, Any]:
    label = t(label_key, language)
    postback = {
        "type": "postback",
        "label": label[:_POSTBACK_LABEL_MAX],
        "data": f"action={action}&appointment_id={reminder_id}",
        "displayText": label,
    }
    if primary:
        return ft.primary_button(label, postback)
    return ft.secondary_button(label, postback)


# postback data 上限 300 字元。醫院名稱 URL 編碼後一個中文字 9 字元，
# 20 字就是 180，加上 action 與掛號 ID 仍在上限內。
_RECORD_HOSPITAL_MAX = 20


def _record_button(
    ft: theme.FlexTheme,
    reminder_id: str,
    hospital_name: str,
    language: Optional[str],
) -> dict[str, Any]:
    """「進診間前按這裡」——在聊天室裡開始看診錄音，不經過選單。

    長輩在診間門口能完成的操作只有一兩下。從掛號提醒直接開始，是整個看診錄音
    功能最可行的入口；要他自己找到功能，實務上不會發生。

    2026-09-22 起錄音改在聊天室（app/services/clinic_transcript/line_flow.py），
    按下去是 postback，回一則徵詢醫師同意的訊息，不再開 LIFF。
    """
    label = t("flex.appt.button.record", language)
    return ft.secondary_button(
        label,
        {
            "type": "postback",
            "label": label[:_POSTBACK_LABEL_MAX],
            "data": clinic_postback_data(
                CLINIC_START_ACTION,
                appointment_id=reminder_id,
                hospital_name=hospital_name[:_RECORD_HOSPITAL_MAX],
            ),
            "displayText": label,
        },
    )


def _footer(contents: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "paddingAll": "lg",
        "spacing": "md",
        "contents": contents,
    }


def _alt_text(base: str, patient_name: Optional[str], language: Optional[str]) -> str:
    if patient_name:
        base = t("flex.appt.alt.family_prefix", language).format(name=patient_name) + base
    return base[:_ALT_TEXT_MAX]


def _message(bubble: dict[str, Any], alt_text: str) -> FlexMessage:
    return FlexMessage(altText=alt_text, contents=FlexContainer.from_dict(bubble))


def build_pre_reminder_flex(
    *,
    reminder_id: str,
    when_text: str,
    hospital_name: str,
    patient_name: Optional[str] = None,
    language: Optional[str] = None,
    font_size: Optional[str] = None,
) -> FlexMessage:
    """T-1h：本人與家屬同一張卡、同一顆「我已出發」。`patient_name` 有值＝家屬版。

    文案不寫「一小時後」：提醒若是門診前 20 分鐘才建立，這張卡會在下一個 tick
    送出，那時已經不是一小時後了。
    """
    ft = theme.resolve_theme(font_size)
    body_text = (
        t("flex.appt.pre_body.family", language).format(name=patient_name)
        if patient_name
        else t("flex.appt.pre_body.self", language)
    )
    bubble = {
        "type": "bubble",
        "header": _header(t("flex.appt.header.pre", language), ft),
        "body": _body(
            [
                _appointment_block(when_text, hospital_name, ft, patient_name, language),
                _paragraph(body_text, ft, margin="md"),
            ]
        ),
        "footer": _footer(
            [
                button
                for button in (
                    _report_button(
                        ft, DEPART_ACTION, reminder_id, "flex.appt.button.depart", language
                    ),
                    # 只在本人版放錄音按鈕。家屬版的收件人不一定會陪去，
                    # 給他一顆按了也沒用的按鈕只會造成誤解。
                    None
                    if patient_name
                    else _record_button(ft, reminder_id, hospital_name, language),
                )
                if button is not None
            ]
        ),
    }
    alt = t("flex.appt.alt.pre", language).format(when=when_text, hospital=hospital_name)
    return _message(bubble, _alt_text(alt, patient_name, language))


def build_start_reminder_flex(
    *,
    reminder_id: str,
    when_text: str,
    hospital_name: str,
    departed: bool,
    patient_name: Optional[str] = None,
    language: Optional[str] = None,
    font_size: Optional[str] = None,
) -> FlexMessage:
    """T+0。已出發：「我已到診」。未出發：催促，並附兩顆按鈕——人可能已經到了
    只是沒按出發（`attend` 允許從 scheduled 直接轉），也可能才正要出門。"""
    ft = theme.resolve_theme(font_size)
    if departed:
        header = _header(t("flex.appt.header.start", language), ft)
        body_key = "flex.appt.start_body.family" if patient_name else "flex.appt.start_body.self"
        buttons = [
            _report_button(ft, ATTEND_ACTION, reminder_id, "flex.appt.button.attend", language)
        ]
        alt_key = "flex.appt.alt.start"
    else:
        header = _header(
            t("flex.appt.header.not_departed", language), ft, background=theme.STATUS_PENDING
        )
        body_key = (
            "flex.appt.not_departed_body.family"
            if patient_name
            else "flex.appt.not_departed_body.self"
        )
        buttons = [
            _report_button(ft, ATTEND_ACTION, reminder_id, "flex.appt.button.attend", language),
            _report_button(
                ft,
                DEPART_ACTION,
                reminder_id,
                "flex.appt.button.depart",
                language,
                primary=False,
            ),
        ]
        alt_key = "flex.appt.alt.not_departed"

    body_text = t(body_key, language)
    if patient_name:
        body_text = body_text.format(name=patient_name)
    bubble = {
        "type": "bubble",
        "header": header,
        "body": _body(
            [
                _appointment_block(when_text, hospital_name, ft, patient_name, language),
                _paragraph(body_text, ft, margin="md"),
            ]
        ),
        "footer": _footer(buttons),
    }
    alt = t(alt_key, language).format(when=when_text, hospital=hospital_name)
    return _message(bubble, _alt_text(alt, patient_name, language))


def build_caregiver_alert_flex(
    *,
    reminder_id: str,
    when_text: str,
    hospital_name: str,
    patient_name: str,
    departed_time: Optional[str] = None,
    language: Optional[str] = None,
    font_size: Optional[str] = None,
) -> FlexMessage:
    """T+30 家屬警報，只送家屬。

    `departed_time` 有值＝已回報出發卻還沒到診：這種情況比從頭沒動作更值得關心
    （可能是路上出了狀況），文案因此分開寫，並把出發時間寫出來。

    附「我已到診」：家屬可能就在長輩身邊，只是兩個人都忘了按。
    """
    ft = theme.resolve_theme(font_size)
    if departed_time:
        body_text = t("flex.appt.caregiver_body.departed", language).format(
            name=patient_name, time=departed_time
        )
    else:
        body_text = t("flex.appt.caregiver_body.not_departed", language).format(
            name=patient_name
        )
    bubble = {
        "type": "bubble",
        "header": _header(
            t("flex.appt.header.caregiver", language), ft, background=theme.STATUS_CLOSED
        ),
        "body": _body(
            [
                _appointment_block(when_text, hospital_name, ft, patient_name, language),
                _paragraph(
                    body_text, ft, color=theme.STATUS_CLOSED, weight="bold", margin="md"
                ),
            ]
        ),
        "footer": _footer(
            [
                _report_button(
                    ft, ATTEND_ACTION, reminder_id, "flex.appt.button.attend", language
                )
            ]
        ),
    }
    alt = t("flex.appt.alt.caregiver", language).format(name=patient_name)
    return _message(bubble, alt[:_ALT_TEXT_MAX])


def build_report_ack_flex(
    *,
    kind: Literal["departed", "attended"],
    when_text: str,
    hospital_name: str,
    reported_time: str,
    reporter_name: str,
    patient_name: Optional[str] = None,
    language: Optional[str] = None,
    font_size: Optional[str] = None,
) -> FlexMessage:
    """按下按鈕之後回覆給按的人的「已記錄」卡片，沒有按鈕。

    LINE 不能修改已經送出的訊息，所以原本那張卡片不會變；這張是新的一則回覆。
    重複按（例如另一位家屬又按了一次）會拿到同一張，上面寫著原本是誰在幾點回報的。
    """
    ft = theme.resolve_theme(font_size)
    reported_line = t("flex.appt.reported_by", language).format(
        time=reported_time, name=reporter_name
    )
    hint_key = (
        "flex.appt.attended_done_hint" if kind == "attended" else "flex.appt.departed_done_hint"
    )
    bubble = {
        "type": "bubble",
        "header": _header(
            t(f"flex.appt.header.{kind}_done", language), ft, background=theme.STATUS_UNKNOWN
        ),
        "body": _body(
            [
                _appointment_block(when_text, hospital_name, ft, patient_name, language),
                _paragraph(t(hint_key, language), ft, margin="md"),
            ]
        ),
        "footer": _footer(
            [
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": theme.NEUTRAL_BG,
                    "cornerRadius": "md",
                    "paddingAll": "lg",
                    "contents": [
                        {
                            "type": "text",
                            "text": reported_line,
                            "color": theme.TEXT_FAINT,
                            "weight": "bold",
                            "size": ft.button,
                            "align": "center",
                            "wrap": True,
                        }
                    ],
                }
            ]
        ),
    }
    alt = t(f"flex.appt.alt.{kind}_done", language).format(hospital=hospital_name)
    return _message(bubble, _alt_text(alt, patient_name, language))
