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
    has_emergency_department,
    resolve_clinic_hours,
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

# 只有這些狀態顯示下次開診時間。營業中不需要；請電洽與無資料本就無可靠時段可講。
_STATUSES_WITH_NEXT_OPEN = frozenset(
    {
        BusinessStatus.BEFORE_OPEN,
        BusinessStatus.BREAK,
        BusinessStatus.CLOSED_TODAY,
        BusinessStatus.CLOSED_DAY,
    }
)

def _build_dot_row(text: str, accent: str) -> dict[str, Any]:
    """圓點＋粗體文字的一列，營業狀態與「設有急診」共用同一種樣式。"""
    return {
        "type": "box",
        "layout": "horizontal",
        "spacing": "xs",
        "alignItems": "center",
        "contents": [
            {
                # 模擬圓點
                "type": "box",
                "layout": "vertical",
                "width": "20px",
                "height": "20px",
                #borderRadius設為寬高的一半就可以模擬圓形效果
                "cornerRadius": "10px",
                "backgroundColor": accent,
                "flex": 0,
                "contents": [{"type": "filler"}],
            },
            {
                "type": "text",
                #故意留空白比較好閱讀
                "text": f"  {text}",
                "size": "xxl",
                "weight": "bold",
                "color": accent,
                "flex": 0,
                "wrap": True,
            },
        ],
    }


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


def _build_status_rows(
    hours: BusinessHoursResult,
    is_emergency: bool,
    language: str | None = None,
) -> dict[str, Any]:
    """
    把已解析好的狀態渲染成 Flex 結構。
    """
    accent, status_key = _STATUS_PRESENTATION[hours.status]

    status_row = _build_dot_row(t(status_key, language), accent)
    rows: list[dict[str, Any]] = [status_row]

    # 下次開診時間改為獨立放在營業狀態下方（第二行）
    if hours.next_open is not None and hours.status in _STATUSES_WITH_NEXT_OPEN:
        rows.append(
            {
                "type": "text",
                "text": _format_next_open(hours.next_open, language),
                "size": "xxl",  
                "weight": "bold",
                "color": accent,
                "margin": "xl",  # 設定上方間距，使其排在狀態列下方
                "wrap": True,
            }
        )

    # 設有急診：獨立一列，藍色圓點，排在最下方
    if is_emergency:
        rows.append(
            {
                **_build_dot_row(
                    t("flex.status.emergency", language), theme.STATUS_EMERGENCY
                ),
                "margin": "sm",
            }
        )

    if len(rows) == 1:
        return rows[0]

    return {
        "type": "box",
        "layout": "vertical",
        "contents": rows,
    }


def _build_status_indicator(
    facility: MedicalFacility,
    language: str | None = None,
) -> dict[str, Any]:
    """
    組出營業狀態標籤（燈號＋狀態文字＋下次開診時間＋設有急診），供簡略卡片與詳情頁共用。
    "設有急診"不再佔用營業狀態那一格，改成獨立的藍色圓點列排在最下面：
    它是能力標示而非營業狀態，兩者本來就該並存。
    """
    return _build_status_rows(
        resolve_clinic_hours(facility),
        has_emergency_department(facility),
        language,
    )


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

    # 呼叫自己內部定義的 UI 專用 URL 函數
    map_uri = _build_flex_map_uri(facility)
    call_label = t("flex.button.call", language)
    map_label = t("flex.button.map", language)

    # 1. 建立必定會顯示的「前往地圖」按鈕
    map_button_box = ft.secondary_button(
        map_label,
        {"type": "uri", "label": map_label, "uri": map_uri},
    )

    # 2. 宣告一個按鈕列表容器，先把地圖按鈕放進去
    buttons_contents: list[dict[str, Any]] = [map_button_box]

    # 3.只有當電話存在，且經過清洗後是有效號碼（長度 >= 6）時，才加入電話按鈕
    if facility.phone:
        tel_uri = _build_flex_tel_uri(facility.phone)
        if tel_uri != "tel:":
            tel_button_box = ft.primary_button(
                call_label,
                {"type": "uri", "label": call_label, "uri": tel_uri},
            )
            # 有電話按鈕時，將它放到地圖按鈕的前面（維持電話在左、地圖在右的順序）
            buttons_contents.insert(0, tel_button_box)

    display_name = facility.name or t("flex.facility.fallback_name", language)

    contents: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": facility.name or t("flex.facility.unknown_name", language),
            "weight": "bold",
            "wrap": True,
            "size": ft.heading,
            "color": theme.TEXT,
        },
        # 第二個位置參數是 language，不是 ft——_build_status_indicator 與它底下的
        # _build_status_rows 都沒有字級參數，詳情頁的呼叫點也是 (facility, language)。
        _build_status_indicator(facility, language),
        {
            "type": "text",
            "text": dist_text,
            "size": ft.body,
            "weight": "bold",
            "color": theme.BRAND_DARK,
        },
        {
            "type": "text",
            "text": facility.address or t("flex.facility.no_address", language),
            "wrap": True,
            "size": ft.body,
            "weight": "bold",
            "color": theme.TEXT_MUTED,
        },
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "md",
            "margin": "lg",
            "contents": buttons_contents,  # 這裡帶入動態組好的按鈕列表
        },
    ]

    # 院所註記（notes）放在整張卡片的最底部、按鈕之後。
    if facility.notes:
        contents.append(
            {
                "type": "text",
                "text": t("flex.facility.note", language).format(note=facility.notes),
                "size": ft.body,
                "weight": "bold",
                "color": "#B71C1C",
                "margin": "lg",
                "wrap": True,
            }
        )

    return {
        "type": "box",
        "layout": "vertical",
        "margin": "xxl",
        "spacing": "md",
        "action": {
            # postback action 會在使用者點擊時，將資料傳回你的 webhook，方便後續回傳對應院所的flex message
            "type": "postback",
            "label": t("flex.action.detail", language),
            "data": f"action=view_facility_detail&facility_id={facility.id}",
            "displayText": t("flex.action.detail_display", language).format(
                name=display_name
            ),
        },
        "contents": contents,
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
            "text": title_text,
            "weight": "bold",
            "size": ft.title,
            "wrap": True,
            "color": theme.TEXT,
        },
        {
            "type": "text",
            "wrap": True,
            "text": subtitle_text,
            "color": theme.TEXT_MUTED,
            "size": ft.body,
        },
        theme.divider("md"),
    ]

    for idx, facility in enumerate(facilities):
        contents.append(create_facility_item_box(facility, ft, language))
        if idx < len(facilities) - 1:
            contents.append(theme.divider("xxl"))

    # 候選清單情境下，若總筆數超過本次顯示筆數，於列表末端補上提示文字
    if is_candidate_list and total_count > len(facilities):
        contents.append(theme.divider("xxl"))
        contents.append(
            {
                "type": "text",
                "text": t("flex.facility.overflow", language),
                "wrap": True,
                "size": ft.body,
                "color": theme.TEXT_FAINT,
                "margin": "xxl",
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
                "spacing": "xl",
                "contents": contents,
            },
        },
    }
