"""血壓、血糖超出範圍的推播卡（health-alerts spec「超出範圍推播的內容」）。

內容：數值、量測時間、等級、被超過的範圍值、代記者（他人代記時）、開啟
健康紀錄頁的按鈕。**刻意不含**診斷、治療建議，也不含緊急撥號區塊——這是
一則「請留意、去看紀錄」的提醒，不是安全通報（design.md 決策 10；
constraints.md「Alerts」）。

版面沿用 ``otc_flex``／``appointment_flex`` 的形狀：header 一句話、body 幾行
``label: value``、footer 一顆按鈕。字級與語言取**收件人自己的**設定——背景
推播沒有 request context，由呼叫端（``health_alert_service``）逐一查出來後
傳入。
"""

from __future__ import annotations

from datetime import timezone
from typing import Any, Optional, Sequence, Tuple

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.i18n import t
from app.models.health import TAIPEI_TZ, HealthMeasurement
from resources.flex_messages import theme

# 等級對應的強調色，只用在內文「被超過的範圍值」那幾列——同
# ``safety_flex``／``otc_flex`` 的版面慣例：header 一律是品牌色，status token
# 保留給內文的局部強調，不是整張卡的底色（見本模組 ``build_health_alert_flex``
# 的 header 區塊）。above／below 共用同一套警示色階，只是換一個方向，沒有
# 臨床嚴重度的意涵，純粹是「請留意」的視覺提示。
_LEVEL_COLOR: dict[str, str] = {
    "above_range": theme.STATUS_CLOSED,
    "below_range": theme.STATUS_PENDING,
}

_FIELD_LABEL_KEY: dict[str, str] = {
    "systolic": "flex.health_alert.field.systolic",
    "diastolic": "flex.health_alert.field.diastolic",
    "glucose": "flex.health_alert.field.glucose",
}


def _measured_at_text(measurement: HealthMeasurement) -> str:
    """量測時間一律以 Asia/Taipei 呈現（health-alerts spec「超出範圍推播的
    內容」）。Motor 存取一圈後常見的 naive datetime 視為 UTC，同
    ``app/models/health.py`` 的既有慣例。
    """
    value = measurement.measured_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(TAIPEI_TZ)
    return local.strftime("%Y/%m/%d %H:%M")


def _row(
    label: str, value: str, ft: theme.FlexTheme, *, color: str = theme.TEXT
) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "baseline",
        "margin": "sm",
        "contents": [
            {
                "type": "text",
                "text": label,
                "size": ft.caption,
                "color": theme.TEXT_MUTED,
                "flex": 2,
                "wrap": True,
            },
            {
                "type": "text",
                "text": value,
                "size": ft.body,
                "color": color,
                "weight": "bold",
                "flex": 3,
                "wrap": True,
                "align": "end",
            },
        ],
    }


def _value_rows(
    measurement: HealthMeasurement, language: Optional[str], ft: theme.FlexTheme
) -> list[dict[str, Any]]:
    """量測本身的數值（不是被超過的範圍值，那是 ``_exceeded_rows`` 的責任）。"""
    if measurement.kind == "blood_pressure":
        rows = [
            _row(t("flex.health_alert.field.systolic", language), f"{measurement.systolic} mmHg", ft),
            _row(t("flex.health_alert.field.diastolic", language), f"{measurement.diastolic} mmHg", ft),
        ]
        if measurement.pulse is not None:
            rows.append(
                _row(t("flex.health_alert.field.pulse", language), str(measurement.pulse), ft)
            )
        return rows

    meal_label = t(f"flex.health_alert.meal.{measurement.meal_context}", language)
    return [
        _row(
            t("flex.health_alert.field.glucose", language),
            f"{measurement.glucose_mg_dl} mg/dL（{meal_label}）",
            ft,
        )
    ]


def _exceeded_rows(
    exceeded: Sequence[Tuple[str, str, int]],
    language: Optional[str],
    ft: theme.FlexTheme,
    color: str,
) -> list[dict[str, Any]]:
    """被超過的每一個範圍值（health-alerts spec：「同時呈現數值與上限」）。"""
    rows = []
    for field, direction, bound in exceeded:
        label = t(_FIELD_LABEL_KEY.get(field, "flex.health_alert.field.glucose"), language)
        key = (
            "flex.health_alert.exceeded.upper"
            if direction == "upper"
            else "flex.health_alert.exceeded.lower"
        )
        rows.append(_row(label, t(key, language).format(bound=bound), ft, color=color))
    return rows


def health_alert_alt_text(alert_key: str, language: Optional[str] = None) -> str:
    """LINE 通知列與鎖定畫面上唯一看得到的字，不含實際數值。"""
    return t(f"flex.health_alert.alt.{alert_key}", language)


def build_health_alert_flex(
    *,
    alert_key: str,
    measurement: HealthMeasurement,
    exceeded: Sequence[Tuple[str, str, int]] = (),
    recorder_name: Optional[str] = None,
    patient_name: Optional[str] = None,
    button_uri: str = "",
    language: Optional[str] = None,
    font_size: Optional[str] = None,
) -> FlexMessage:
    """超出範圍卡。

    ``alert_key`` 是 ``bp_high``／``bp_low``／``glucose_high``／
    ``glucose_low`` 之一，決定 header 與 altText 的文案。

    ``patient_name`` 只在收件人不是本人時才傳入（家人收到的卡要知道是誰的
    紀錄；本人自己的卡不需要看到自己的名字）。``recorder_name`` 只在
    ``recorded_by != user_id``（他人代記）時才傳入。

    ``button_uri`` 省略時不顯示按鈕（呼叫端在 ``LIFF_URL`` 未設定時傳空字串，
    比照其餘推播服務的慣例）。
    """
    ft = theme.resolve_theme(font_size)
    # 只用在內文「被超過的範圍值」那幾列的局部強調色，不是整張卡的底色
    # （見本檔案頂端的說明；同 safety_flex／otc_flex 的 header／內文分工）。
    emphasis_color = _LEVEL_COLOR.get(measurement.level, theme.BRAND)

    body_contents: list[dict[str, Any]] = []
    if patient_name:
        body_contents.append(
            {
                "type": "text",
                "text": patient_name,
                "weight": "bold",
                "size": ft.title,
                "color": theme.TEXT,
                "wrap": True,
            }
        )
    body_contents.append(
        _row(t("flex.health_alert.label.measured_at", language), _measured_at_text(measurement), ft)
    )
    body_contents.extend(_value_rows(measurement, language, ft))
    body_contents.extend(_exceeded_rows(exceeded, language, ft, emphasis_color))
    if recorder_name:
        body_contents.append(
            {
                "type": "text",
                "text": t("flex.health_alert.recorded_by", language).format(name=recorder_name),
                "size": ft.caption,
                "color": theme.TEXT_MUTED,
                "wrap": True,
                "margin": "md",
            }
        )

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
                    "text": t(f"flex.health_alert.header.{alert_key}", language),
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
            "spacing": "sm",
            "contents": body_contents,
        },
    }

    if button_uri:
        label = t("flex.health_alert.button.open_records", language)
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "lg",
            "contents": [
                ft.primary_button(label, {"type": "uri", "label": label, "uri": button_uri})
            ],
        }

    return FlexMessage(
        altText=health_alert_alt_text(alert_key, language),
        contents=FlexContainer.from_dict(bubble),
    )
