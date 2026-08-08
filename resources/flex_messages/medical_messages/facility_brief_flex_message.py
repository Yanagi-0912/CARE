"""
在這裡包裝醫療院所資訊成flex message,套用的flex message模板是醫療院所
的簡略資訊(名稱、距離、地址、地圖按鈕、電話按鈕)，不包含診療科別、營業時間等詳細資訊。

"""

import re
import urllib.parse
from typing import Any
from app.schemas import MedicalFacility

from app.i18n import t
from app.services.medical.business_hours import (
    BusinessHoursResult,
    BusinessStatus,
    NextOpen,
    resolve_business_hours,
)
from resources.flex_messages import theme

def _build_flex_map_uri(facility: MedicalFacility) -> str:
    """生成最符合 LINE 導航按鈕規格的 Google Map 連結"""
    # 優先級:經緯度->名稱->地址
    if facility.latitude and facility.longitude:
        query = f"{facility.latitude},{facility.longitude}"
    elif facility.name:
        query = facility.name
    elif facility.address:
        query = facility.address
    else:
        query = "醫療院所"

    encoded_query = urllib.parse.quote(query)
    # 設定dir直接導航到該地點， travelmode=driving 預設為開車
    return f"https://www.google.com/maps/dir/?api=1&destination={encoded_query}&travelmode=driving"


def _build_flex_tel_uri(phone: str | None) -> str:
    """處理 Flex Message 撥號按鈕專用的電話格式"""
    if not phone:
        return "tel:"
    digits = re.sub(r"\D", "", phone)
    return f"tel:{digits}" if len(digits) >= 6 else "tel:"

# 狀態 → (色彩, i18n key)。午休與請電洽用琥珀色，與紅色的休診區隔；
# 急診用藍色，避免與綠色的「營業中」混淆——那是能力標示，不是營業狀態。
_STATUS_PRESENTATION: dict[BusinessStatus, tuple[str, str]] = {
    BusinessStatus.OPEN: (theme.STATUS_OPEN, "flex.status.open"),
    BusinessStatus.BEFORE_OPEN: (theme.STATUS_PENDING, "flex.status.before_open"),
    BusinessStatus.BREAK: (theme.STATUS_PENDING, "flex.status.break"),
    BusinessStatus.CLOSED_TODAY: (theme.STATUS_CLOSED, "flex.status.closed_today"),
    BusinessStatus.CLOSED_DAY: (theme.STATUS_CLOSED, "flex.status.closed_day"),
    BusinessStatus.EMERGENCY: (theme.STATUS_EMERGENCY, "flex.status.emergency"),
    BusinessStatus.CALL_AHEAD: (theme.STATUS_PENDING, "flex.status.call_ahead"),
    BusinessStatus.UNKNOWN: (theme.STATUS_UNKNOWN, "flex.status.unknown"),
}

# 只有這些狀態顯示下次開診時間。營業中不需要；請電洽與無資料本就無可靠時段可講；
# 急診刻意不顯示——在「設有急診」旁邊放門診時間，會被誤讀成急診那時才開。
_STATUSES_WITH_NEXT_OPEN = frozenset(
    {
        BusinessStatus.BEFORE_OPEN,
        BusinessStatus.BREAK,
        BusinessStatus.CLOSED_TODAY,
        BusinessStatus.CLOSED_DAY,
    }
)


def _format_next_open(next_open: NextOpen, language: str | None = None) -> str:
    """組出「今日 14:00 開診」或「週四 08:00 開診」。"""
    if next_open.is_today:
        return t("flex.status.next_open_today", language).format(
            time=next_open.time_text
        )
    return t("flex.status.next_open_day", language).format(
        day=t(f"weekday.{next_open.weekday_key}", language),
        time=next_open.time_text,
    )


def _build_status_indicator(
    hours: BusinessHoursResult,
    ft: theme.FlexTheme,
    language: str | None = None,
) -> dict[str, Any]:
    """
    組出營業狀態標籤，供簡略卡片與詳情頁共用。

    休診時附上下次開診時間——使用者真正在問的是「我什麼時候能去」，
    只說「休診中」等於沒回答。
    """
    accent, status_key = _STATUS_PRESENTATION[hours.status]
    status_text = t(status_key, language)

    rows: list[dict[str, Any]] = [
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "alignItems": "center",
            "contents": [
                {
                    # 細長色條，比圓點更俐落且渲染穩定
                    "type": "box",
                    "layout": "vertical",
                    "width": "6px",
                    "height": "22px",
                    "cornerRadius": "3px",
                    "backgroundColor": accent,
                    "flex": 0,
                    "contents": [{"type": "filler"}],
                },
                {
                    "type": "text",
                    "text": status_text,
                    "size": ft.body,
                    "weight": "bold",
                    "color": accent,
                    "flex": 0,
                    "wrap": True,
                },
            ],
        }
    ]

    if hours.next_open is not None and hours.status in _STATUSES_WITH_NEXT_OPEN:
        rows.append(
            {
                "type": "text",
                "text": _format_next_open(hours.next_open, language),
                "size": ft.caption,
                "color": theme.TEXT_MUTED,
                "margin": "xs",
                "wrap": True,
            }
        )

    if hours.note:
        # clinicTime 不知道春節。註記原文一律顯示，讓使用者自行判斷是否適用今天。
        rows.append(
            {
                "type": "text",
                "text": t("flex.facility.note", language).format(note=hours.note),
                "size": ft.caption,
                "color": theme.TEXT_FAINT,
                "margin": "xs",
                "wrap": True,
            }
        )

    if len(rows) == 1:
        return rows[0]

    return {
        "type": "box",
        "layout": "vertical",
        "contents": rows,
    }


def create_facility_item_box(
    facility: MedicalFacility,
    ft: theme.FlexTheme | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """建立單一醫療院所的 Flex Message Box 結構"""
    ft = ft or theme.resolve_theme()

    if facility.distance_meters is None:
        dist_text = t("flex.facility.distance_unknown", language)
    elif facility.distance_meters >= 1000:
        dist_text = t("flex.facility.distance_km", language).format(
            value=f"{facility.distance_meters / 1000:.1f}"
        )
    else:
        dist_text = t("flex.facility.distance_m", language).format(
            value=f"{facility.distance_meters:.0f}"
        )

    map_uri = _build_flex_map_uri(facility)
    call_label = t("flex.button.call", language)
    map_label = t("flex.button.map", language)

    buttons_contents: list[dict[str, Any]] = []

    # 電話存在且清洗後為有效號碼（長度 >= 6）時才顯示撥號按鈕
    if facility.phone:
        tel_uri = _build_flex_tel_uri(facility.phone)
        if tel_uri != "tel:":
            buttons_contents.append(
                ft.primary_button(
                    call_label,
                    {"type": "uri", "label": call_label, "uri": tel_uri},
                )
            )

    buttons_contents.append(
        ft.secondary_button(
            map_label,
            {"type": "uri", "label": map_label, "uri": map_uri},
        )
    )

    display_name = facility.name or t("flex.facility.fallback_name", language)

    return {
        "type": "box",
        "layout": "vertical",
        "margin": "lg",
        "paddingAll": "lg",
        "backgroundColor": theme.SURFACE_ALT,
        "cornerRadius": "lg",
        "spacing": "sm",
        "action": {
            # postback 會把資料回傳 webhook，用來回覆對應院所的詳情卡
            "type": "postback",
            "label": t("flex.action.detail", language),
            "data": f"action=view_facility_detail&facility_id={facility.id}",
            "displayText": t("flex.action.detail_display", language).format(
                name=display_name
            ),
        },
        "contents": [
            {
                "type": "text",
                "text": facility.name or t("flex.facility.unknown_name", language),
                "weight": "bold",
                "wrap": True,
                "size": ft.heading,
                "color": theme.TEXT,
            },
            _build_status_indicator(resolve_business_hours(facility), ft, language),
            {
                "type": "text",
                "text": dist_text,
                "size": ft.body,
                "weight": "bold",
                "color": theme.BRAND_DARK,
                "margin": "xs",
            },
            {
                "type": "text",
                "text": facility.address or t("flex.facility.no_address", language),
                "wrap": True,
                "size": ft.body,
                "color": theme.TEXT_MUTED,
            },
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "margin": "lg",
                "contents": buttons_contents,
            },
        ],
    }


def generate_facility_list_flex_message(
    facilities: list[MedicalFacility],
    total_count: int | None = None,
    language: str | None = None,
    font_size: str | None = None,
    title_override: str | None = None,
    subtitle_override: str | None = None,
) -> dict[str, Any]:
    """
    根據醫療院所列表，動態渲染完整的 LINE Flex Message 物件結構 (含 Wrapper)。

    total_count 為 None 時，視為「附近搜尋」情境，標題固定顯示「附近醫療院所」。
    total_count 有值時，視為「名稱查詢候選清單」情境，標題改為「找到多筆相似院所」，
    並在 total_count 大於實際顯示筆數時，於卡片列表末端加入提示文字。

    科別搜尋情境需要說明「查的是哪一科」與「搜到多遠」，這類脈絡無法由筆數推導，
    因此開放 title_override／subtitle_override 由呼叫端直接指定文案。
    """
    ft = theme.resolve_theme(font_size)

    is_candidate_list = total_count is not None
    # 這裡的 is_candidate_list 變數用來判斷是否為候選清單情境，影響標題與提示文字的顯示
    if is_candidate_list:
        title_text = t("flex.facility.title.candidates", language)
        subtitle_text = t("flex.facility.subtitle.candidates", language)
    else:
        title_text = t("flex.facility.title.nearby", language)
        subtitle_text = t("flex.facility.subtitle.nearby", language)
    subtitle_text = subtitle_text.format(count=len(facilities))

    if title_override:
        title_text = title_override
    if subtitle_override:
        subtitle_text = subtitle_override

    contents: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": t("flex.facility.eyebrow", language),
            "weight": "bold",
            "size": ft.caption,
            "color": theme.BRAND,
        },
        {
            "type": "text",
            "text": title_text,
            "weight": "bold",
            "size": ft.title,
            "wrap": True,
            "color": theme.TEXT,
            "margin": "xs",
        },
        {
            "type": "text",
            "wrap": True,
            "text": subtitle_text,
            "color": theme.TEXT_MUTED,
            "size": ft.body,
            "margin": "sm",
        },
        theme.divider("lg"),
    ]

    # 每張院所卡片自帶底色與圓角，彼此以間距區隔，不再需要分隔線
    for facility in facilities:
        contents.append(create_facility_item_box(facility, ft, language))

    # 候選清單情境下，若總筆數超過本次顯示筆數，於列表末端補上提示文字
    if is_candidate_list and total_count > len(facilities):
        contents.append(
            {
                "type": "text",
                "text": t("flex.facility.overflow", language),
                "wrap": True,
                "size": ft.caption,
                "color": theme.TEXT_FAINT,
                "margin": "lg",
            }
        )

    return {
        "type": "flex",
        "altText": t("flex.facility.alt", language),
        "contents": {
            "type": "bubble",
            "size": "giga",
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "xl",
                "backgroundColor": theme.SURFACE,
                "contents": contents,
            },
        },
    }
