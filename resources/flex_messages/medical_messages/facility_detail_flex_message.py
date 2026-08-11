"""
單一醫療院所詳情版 Flex Message 模板，包含營業時間表格與診療科別網格，
供 lookup_medical_facility 命中單筆結果時使用。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any
from app.i18n import department_label, t
from app.schemas import MedicalFacility
from app.services.medical.business_hours import TAIPEI_TZ, WEEKDAY_KEYS
from resources.flex_messages import theme
from resources.flex_messages.medical_messages.facility_brief_flex_message import (
    _build_flex_map_uri,
    _build_flex_tel_uri,
    _build_status_indicator,
)

# 診療科別網格每列顯示筆數
DEPARTMENTS_PER_ROW = 3

# 門診表的時段列：(列名的 i18n key, 起, 迄)。依 slot 的「開始時間」落在哪一段來分類
_CLINIC_PERIODS: tuple[tuple[str, str, str], ...] = (
    ("flex.detail.period.morning", "00:00", "12:00"),
    ("flex.detail.period.afternoon", "12:00", "17:00"),
    ("flex.detail.period.evening", "17:00", "24:00"),
)

# 今天那一欄的底色。
_TODAY_COLUMN_BG = "#FFE082"
# 今天的欄頭文字、以及與代表時間不同的例外時間
_TODAY_ACCENT = "#B26A00"
# 其餘六欄沿用原本的斑馬紋配色
_ZEBRA_COLUMN_BG = ("#E8F5E9", theme.SURFACE)

_HAS_CLINIC_MARK = "●"
_NO_CLINIC_MARK = "-"


def _period_index(open_text: str) -> int | None:
    """slot 的開始時間屬於哪一個時段列；落在所有區間之外時回 None（不畫進表裡）。"""
    for index, (_label, start, end) in enumerate(_CLINIC_PERIODS):
        if start <= open_text < end:
            return index
    return None


def _collect_period_ranges(
    clinic_time: dict[str, Any],
) -> dict[int, dict[str, list[tuple[str, str]]]]:
    """
    整理成「時段 → 星期 → 該時段的時間區間」。
    同一天同一時段可能有多個 slot（少見但資料裡確實有），一律保留，
    後續呈現時整組一起比對與顯示，不會自作主張合併成一個大區間。
    """
    table: dict[int, dict[str, list[tuple[str, str]]]] = {}
    for day_key in WEEKDAY_KEYS:
        day = clinic_time.get(day_key)
        if day is None or day.isClosed:
            continue
        for slot in day.slots:
            if not (slot.open and slot.close):
                continue
            index = _period_index(slot.open)
            if index is None:
                continue
            table.setdefault(index, {}).setdefault(day_key, []).append(
                (slot.open, slot.close)
            )
    return table


def _representative_ranges(
    day_ranges: dict[str, list[tuple[str, str]]],
) -> list[tuple[str, str]]:
    """
    該時段的代表時間＝出現最多天的那一組時間，寫在最左欄當作這一列的標題時間。

    取眾數而不是取第一天，是因為「週一比較特別、其餘六天一致」的院所不少；
    用週一當代表會讓六天都被標成例外，整張表被小字淹沒。
    """
    counter = Counter(tuple(ranges) for ranges in day_ranges.values())
    return list(counter.most_common(1)[0][0])


def _format_ranges(ranges: list[tuple[str, str]]) -> str:
    #把時間區間排成上下兩行（開始／結束）。

    return "\n".join(f"{open_text}\n{close_text}" for open_text, close_text in ranges)


def _column_bg(day_index: int, day_key: str, today_key: str) -> str:
    return _TODAY_COLUMN_BG if day_key == today_key else _ZEBRA_COLUMN_BG[day_index % 2]


def _table_cell(contents: list[dict[str, Any]], bg_color: str) -> dict[str, Any]:
    """表格的一格。欄與欄之間不留間距，同一欄的底色才會連成一條直的色帶。"""
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": bg_color,
        "paddingAll": "sm",
        "flex": 1,
        "contents": contents,
    }


def _row_label_cell(
    title: str, subtitle: str | None, ft: theme.FlexTheme
) -> dict[str, Any]:
    """最左欄：時段名稱與該時段的代表時間。"""
    contents: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": title,
            "size": ft.body,
            "weight": "bold",
            "color": theme.BRAND_DARK,
            "align": "center",
            "wrap": True,
        }
    ]
    if subtitle:
        contents.append(
            {
                "type": "text",
                "text": subtitle,
                "size": ft.caption,
                "weight": "bold",
                "color": theme.TEXT_MUTED,
                "align": "center",
                "wrap": True,
            }
        )
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": theme.SURFACE,
        "paddingAll": "sm",
        "flex": 3,  # 要放得下「早診」與「09:30 12:30」，比單一星期欄寬
        "contents": contents,
    }


def _build_clinic_time_rows(
    clinic_time: dict[str, Any] | None,
    ft: theme.FlexTheme | None = None,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """
    組出「時段 × 星期」的門診表：每一列是早／午／晚診，每一欄是星期幾，
    有診打圓點、沒診打「-」，今天那一欄整條上色。
    """
    ft = ft or theme.resolve_theme()
    if not clinic_time:
        return []

    table = _collect_period_ranges(clinic_time)
    if not table:
        # 有 clinicTime 但七天都休診或都沒有有效時段：畫一張全是「-」的空表沒有意義，
        # 交給呼叫端顯示「無資料」。
        return []

    today_key = WEEKDAY_KEYS[datetime.now(TAIPEI_TZ).weekday()]
    day_columns = list(enumerate(WEEKDAY_KEYS))

    # 表頭：最左欄留給「門診表」，其餘七欄放各語言的極短星期（中文「一」、英文「Mo」…）。
    # 欄寬只有整表的十分之一，用一般的「週一」「Mon」會擠成兩行。
    header_cells: list[dict[str, Any]] = [
        _row_label_cell(t("flex.detail.clinic_table", language), None, ft)
    ]
    for day_index, day_key in day_columns:
        is_today = day_key == today_key
        header_cells.append(
            _table_cell(
                [
                    {
                        "type": "text",
                        "text": t(f"weekday.short.{day_key}", language),
                        "size": ft.body,
                        "weight": "bold",
                        "color": _TODAY_ACCENT if is_today else theme.BRAND_DARK,
                        "align": "center",
                    }
                ],
                _column_bg(day_index, day_key, today_key),
            )
        )

    rows: list[dict[str, Any]] = [
        {"type": "box", "layout": "horizontal", "contents": header_cells}
    ]

    for period_index, (period_key, _start, _end) in enumerate(_CLINIC_PERIODS):
        day_ranges = table.get(period_index)
        if not day_ranges:
            # 整週都沒有這個時段（多數診所沒有晚診），整列不畫，不留空列。
            continue

        representative = _representative_ranges(day_ranges)
        cells: list[dict[str, Any]] = [
            _row_label_cell(
                t(period_key, language), _format_ranges(representative), ft
            )
        ]

        for day_index, day_key in day_columns:
            ranges = day_ranges.get(day_key)
            bg_color = _column_bg(day_index, day_key, today_key)

            if not ranges:
                # 休診、或當天沒有這個時段的診。缺這一天的資料時同樣落在這裡——
                # 資料上兩者無法區分，一律以「沒有診」呈現。
                cells.append(
                    _table_cell(
                        [
                            {
                                "type": "text",
                                "text": _NO_CLINIC_MARK,
                                "size": ft.body,
                                "weight": "bold",
                                "color": theme.TEXT_FAINT,
                                "align": "center",
                            }
                        ],
                        bg_color,
                    )
                )
                continue

            cell_contents: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": _HAS_CLINIC_MARK,
                    "size": ft.body,
                    "color": theme.BRAND_DARK,
                    "align": "center",
                }
            ]
            if ranges != representative:
                cell_contents.append(
                    {
                        # 例外時間是整張卡片唯一不隨字級設定放大的文字：它擠在只有
                        # 十分之一表寬的格子裡，跟著放大會把七欄撐爆、整張表變形。
                        # 寧可這一行維持最小可讀字級，也不要毀掉表格結構。
                        "type": "text",
                        "text": _format_ranges(ranges),
                        "size": "xxs",
                        "weight": "bold",
                        "color": _TODAY_ACCENT,  # 與代表時間不同，用琥珀色提醒這天是例外
                        "align": "center",
                        "wrap": True,
                    }
                )
            cells.append(_table_cell(cell_contents, bg_color))

        rows.append({"type": "box", "layout": "horizontal", "contents": cells})

    return rows


def _department_chip(
    name: str, bg_color: str, ft: theme.FlexTheme, language: str | None = None
) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": bg_color,  # 由該列決定卡片的填色
        "cornerRadius": "md",
        "paddingAll": "lg",  # 加大內距，使不可點擊的科別標籤上下高度更厚實易讀
        "flex": 1,
        "contents": [
            {
                "type": "text",
                # 科別是封閉的部定專科清單，可以翻譯；字典裡沒有的（次專科、髒資料）
                # 由 department_label() 原樣回傳中文，見該函式的說明。
                "text": department_label(name, language),
                "size": ft.body,
                "color": theme.TEXT,  # 文字顏色加深
                "align": "center",
                "weight": "bold",
                "wrap": True,
            }
        ],
    }


def _build_department_grid(
    departments: list[str] | None,
    ft: theme.FlexTheme | None = None,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """組出完整診療科別網格"""
    ft = ft or theme.resolve_theme()

    if not departments:
        return [
            {
                "type": "text",
                "text": t("flex.detail.no_data", language),
                "size": ft.body,
                "color": theme.TEXT,
            }
        ]

    rows: list[dict[str, Any]] = []

    # 依每列顯示筆數切分
    for row_index in range(0, len(departments), DEPARTMENTS_PER_ROW):
        row_departments = departments[row_index : row_index + DEPARTMENTS_PER_ROW]

        # 計算當前是第幾列
        row_number = row_index // DEPARTMENTS_PER_ROW

        # 以「列」為單位交替卡片顏色：一列淺灰色，一列米黃色
        current_row_bg = "#EEEEEE" if row_number % 2 == 0 else "#FFFDE7"

        # 生成當前列的所有卡片
        row_chips: list[dict[str, Any]] = [
            _department_chip(name, current_row_bg, ft, language)
            for name in row_departments
        ]

        # 若不滿一列，補上 filler 填滿空間
        while len(row_chips) < DEPARTMENTS_PER_ROW:
            row_chips.append({"type": "filler"})

        rows.append(
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": "md",  # 加大方塊左右間隔
                "margin": "md",  # 增加上下列之間的行距
                "contents": row_chips,
            }
        )

    return rows


def generate_facility_detail_flex_message(
    facility: MedicalFacility,
    language: str | None = None,
    font_size: str | None = None,
) -> dict[str, Any]:
    """
    根據單一醫療院所完整資料，動態渲染詳情版 Flex Message。

    language／font_size 省略時讀 request-scoped 的 ContextVar（webhook 進來時由
    dispatcher 依使用者設定寫入），所以正常流程不必層層傳參；留著參數是為了讓
    測試與其他呼叫端能明確指定。
    """
    ft = theme.resolve_theme(font_size)
    map_uri = _build_flex_map_uri(facility)

    header_contents: list[dict[str, Any]] = []
    if facility.type:
        header_contents.append(
            {
                "type": "text",
                "text": facility.type,
                "size": ft.body,
                "weight": "bold",
                "color": theme.BRAND,
            }
        )
    header_contents.append(
        {
            "type": "text",
            # 院所名稱與地址是資料庫原文（中文），不翻譯；只有缺值時的替代字走 i18n
            "text": facility.name or t("flex.facility.unknown_name", language),
            "weight": "bold",
            "wrap": True,
            "size": ft.title,
            "color": theme.TEXT,
        }
    )
    header_contents.append(_build_status_indicator(facility, language))
    body_contents: list[dict[str, Any]] = [
        {
            "type": "box",
            "layout": "vertical",
            "spacing": "lg",
            "contents": header_contents,
        },
        {
            "type": "text",
            "text": facility.address or t("flex.facility.no_address", language),
            "wrap": True,
            "size": ft.heading,
            "weight": "bold",
            "color": theme.TEXT_MUTED,
            "margin": "lg",
        },
    ]

    # 電話號碼可點擊撥號；無有效電話時僅顯示純文字，不加 action
    phone_text_block: dict[str, Any] = {
        "type": "text",
        "text": facility.phone or t("flex.detail.no_data", language),
        "size": ft.heading,
        "margin": "lg",  # 地址↔電話：xs(2px) × 1.5
    }
    tel_uri = _build_flex_tel_uri(facility.phone)
    call_label = t("flex.button.call", language)
    map_label = t("flex.button.map", language)
    if tel_uri != "tel:":
        phone_text_block.update(
            {
                "color": theme.BRAND_DARK,
                "weight": "bold",
                "decoration": "underline",
                "action": {"type": "uri", "label": call_label, "uri": tel_uri},
            }
        )
    else:
        phone_text_block["color"] = theme.TEXT_MUTED
    body_contents.append(phone_text_block)

    # 院所註記（notes）排在電話與營業時間之間：它多半是在補充營業時間表講不清楚的事
    # （例如「星期一至星期日8:30~12:00 13:30~17:30 18:00~21:00」「春節假期休診」），
    # 放在時間表正上方，使用者讀到表格時剛好帶著這層前提。沒有註記就整塊不產生。
    # 註記原文一律照登——clinicTime 不知道春節，是否適用今天交由使用者自行判斷。
    if facility.notes:
        body_contents.append(
            {
                "type": "text",
                # 註記原文是資料庫的中文，不翻譯；只有前綴（「院所註記：」）走 i18n
                "text": t("flex.facility.note", language).format(note=facility.notes),
                "wrap": True,
                "weight": "bold",
                "color": "#B71C1C",
                "size": ft.body,
                "margin": "lg",
            }
        )

    body_contents.append({"type": "separator", "margin": "lg", "color": "#CCCCCC"})

    clinic_time_rows = _build_clinic_time_rows(facility.clinic_time, ft, language)
    body_contents.append(
        {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "contents": [
                {
                    "type": "text",
                    "text": t("flex.detail.hours", language),
                    "weight": "bold",
                    "size": ft.heading,
                    "color": theme.TEXT,
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "md",
                    "cornerRadius": "md",
                    "contents": clinic_time_rows
                    or [
                        {
                            "type": "text",
                            "text": t("flex.detail.no_data", language),
                            "weight": "bold",
                            "size": ft.body,
                            "color": theme.TEXT_MUTED,
                        }
                    ],
                },
            ],
        }
    )

    body_contents.append({"type": "separator", "margin": "lg", "color": "#CCCCCC"})

    department_count = len(facility.departments) if facility.departments else 0
    body_contents.append(
        {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": t("flex.detail.departments", language).format(
                        count=department_count
                    ),
                    "weight": "bold",
                    "size": ft.heading,
                    "color": theme.TEXT,
                },
                *_build_department_grid(facility.departments, ft, language),
            ],
        }
    )

    footer_contents: list[dict[str, Any]] = []
    if tel_uri != "tel:":
        footer_contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": theme.BRAND,
                "cornerRadius": "md",
                "paddingAll": "lg",  # 按鈕內距加大，擴大長輩點擊判定面積
                "flex": 1,
                "action": {"type": "uri", "label": call_label, "uri": tel_uri},
                "contents": [
                    {
                        "type": "text",
                        "text": f"📞{call_label}",
                        "color": theme.TEXT_ON_BRAND,
                        "weight": "bold",
                        "size": ft.button,
                        "align": "center",
                        "wrap": True,
                    }
                ],
            }
        )
    footer_contents.append(
        {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": theme.NEUTRAL_BG,
            "cornerRadius": "md",
            "paddingAll": "lg",  # 按鈕內距加大，擴大長輩點擊判定面積
            "flex": 1,
            "action": {"type": "uri", "label": map_label, "uri": map_uri},
            "contents": [
                {
                    "type": "text",
                    "text": map_label,
                    "color": theme.TEXT,
                    "weight": "bold",
                    "size": ft.button,
                    "align": "center",
                    "wrap": True,
                }
            ],
        }
    )

    return {
        "type": "flex",
        "altText": t("flex.detail.alt", language).format(
            name=facility.name or t("flex.facility.eyebrow", language)
        ),
        "contents": {
            "type": "bubble",
            "size": "giga",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": body_contents,
            },
            "footer": {
                "type": "box",
                "layout": "horizontal",
                "spacing": "md",
                "contents": footer_contents,
            },
        },
    }
