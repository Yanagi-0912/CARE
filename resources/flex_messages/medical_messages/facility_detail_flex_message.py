"""
單一醫療院所詳情版 Flex Message 模板，包含營業時間表格與診療科別網格，
供 lookup_medical_facility 命中單筆結果時使用。
"""

from __future__ import annotations

from typing import Any
from app.schemas import MedicalFacility
from app.services.medical.medical_service import WEEKDAY_LABELS
from resources.flex_messages.medical_messages.facility_brief_flex_message import (
    _build_flex_map_uri,
    _build_flex_tel_uri,
)

# 診療科別網格每列顯示筆數
DEPARTMENTS_PER_ROW = 3


def _build_clinic_time_rows(clinic_time: dict[str, Any] | None) -> list[dict[str, Any]]:
    """依 WEEKDAY_LABELS 順序，組出營業時間表格的每一列（含分隔線與斑馬紋底色）"""
    rows: list[dict[str, Any]] = []
    if not clinic_time:
        return rows

    weekday_keys = list(WEEKDAY_LABELS.items())
    for idx, (day_key, label) in enumerate(weekday_keys):
        day = clinic_time.get(day_key)
        if day is None:
            continue

        if day.isClosed:
            time_text = "休診"
        else:
            ranges = [
                f"{slot.open}-{slot.close}"
                for slot in day.slots
                if slot.open and slot.close
            ]
            time_text = "、".join(ranges) if ranges else "無資料"

        # 斑馬紋底色，單雙數列交替
        bg_color = "#E8F5E9" if idx % 2 == 0 else "#FFFFFF"

        if rows:
            rows.append({"type": "separator", "color": "#DDDDDD"})

        rows.append(
            {
                "type": "box",
                "layout": "horizontal",
                "paddingAll": "md",
                "backgroundColor": bg_color,
                "spacing": "lg",  # 間距稍大，盡量不要改了
                "contents": [
                    {
                        "type": "text",
                        "text": label,
                        "size": "lg",
                        "weight": "bold",
                        "color": "#1B5E20",
                        "flex": 1,
                    },
                    {
                        "type": "text",
                        "text": time_text,
                        "weight": "bold",
                        "size": "lg",
                        "color": "#231818",
                        "flex": 3,  # flex 3不會overflow
                        "wrap": True,  # 允許自動折行，避免三段時間導致 overflow
                    },
                ],
            }
        )
    return rows


def _department_chip(name: str, bg_color: str) -> dict[str, Any]:
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
                "text": name,
                "size": "lg",  # 字級保持 lg
                "color": "#111111",  # 文字顏色加深
                "align": "center",
                "weight": "bold",
                "wrap": True,
            }
        ],
    }


def _build_department_grid(
    departments: list[str] | None, facility_name: str
) -> list[dict[str, Any]]:
    """組出完整診療科別網格"""

    if not departments:
        return [
            {
                "type": "text",
                "text": "無資料",
                "size": "lg",
                "color": "#111111",
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
            _department_chip(name, bg_color=current_row_bg) for name in row_departments
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


def generate_facility_detail_flex_message(facility: MedicalFacility) -> dict[str, Any]:
    """根據單一醫療院所完整資料，動態渲染詳情版 Flex Message"""
    map_uri = _build_flex_map_uri(facility)

    header_contents: list[dict[str, Any]] = []
    if facility.type:
        header_contents.append(
            {
                "type": "text",
                "text": facility.type,
                "size": "lg",
                "weight": "bold",
                "color": "#2E7D32",
            }
        )
    header_contents.append(
        {
            "type": "text",
            "text": facility.name or "未知名稱",
            "weight": "bold",
            "wrap": True,
            "size": "3xl",
            "color": "#111111",
        }
    )

    body_contents: list[dict[str, Any]] = [
        {
            "type": "box",
            "layout": "vertical",
            "spacing": "xs",
            "contents": header_contents,
        },
        {
            "type": "text",
            "text": facility.address or "暫無地址資訊",
            "wrap": True,
            "size": "xl",
            "color": "#555555",
            "margin": "sm",
        },
    ]

    # 電話號碼可點擊撥號；無有效電話時僅顯示純文字，不加 action
    phone_text_block: dict[str, Any] = {
        "type": "text",
        "text": facility.phone or "無資料",
        "size": "xl",
        "margin": "xs",
    }
    tel_uri = _build_flex_tel_uri(facility.phone)
    if tel_uri != "tel:":
        phone_text_block.update(
            {
                "color": "#1B5E20",
                "weight": "bold",
                "decoration": "underline",
                "action": {"type": "uri", "label": "撥打電話", "uri": tel_uri},
            }
        )
    else:
        phone_text_block["color"] = "#555555"
    body_contents.append(phone_text_block)

    body_contents.append({"type": "separator", "margin": "lg", "color": "#CCCCCC"})

    clinic_time_rows = _build_clinic_time_rows(facility.clinic_time)
    body_contents.append(
        {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "contents": [
                {
                    "type": "text",
                    "text": "營業時間",
                    "weight": "bold",
                    "size": "xl",
                    "color": "#111111",
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
                            "text": "無資料",
                            "weight": "bold",
                            "size": "lg",
                            "color": "#333333",
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
                    "text": f"診療科別（共 {department_count} 項）",
                    "weight": "bold",
                    "size": "xl",
                    "color": "#111111",
                },
                *_build_department_grid(
                    facility.departments, facility.name or "此院所"
                ),
            ],
        }
    )

    footer_contents: list[dict[str, Any]] = []
    if tel_uri != "tel:":
        footer_contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#2E7D32",
                "cornerRadius": "md",
                "paddingAll": "lg",  # 按鈕內距加大，擴大長輩點擊判定面積
                "flex": 1,
                "action": {"type": "uri", "label": "撥打電話", "uri": tel_uri},
                "contents": [
                    {
                        "type": "text",
                        "text": "📞撥打電話",
                        "color": "#FFFFFF",
                        "weight": "bold",
                        "size": "xl",
                        "align": "center",
                    }
                ],
            }
        )
    footer_contents.append(
        {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#E0E0E0",
            "cornerRadius": "md",
            "paddingAll": "lg",  # 按鈕內距加大，擴大長輩點擊判定面積
            "flex": 1,
            "action": {"type": "uri", "label": "前往地圖", "uri": map_uri},
            "contents": [
                {
                    "type": "text",
                    "text": "前往地圖",
                    "color": "#111111",
                    "weight": "bold",
                    "size": "xl",
                    "align": "center",
                }
            ],
        }
    )

    return {
        "type": "flex",
        "altText": f"{facility.name or '醫療院所'}詳細資訊",
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
