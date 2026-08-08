"""
單一醫療院所詳情版 Flex Message 模板，包含營業時間表格與診療科別網格，
供 lookup_medical_facility 命中單筆結果時使用。
"""

from __future__ import annotations

from typing import Any
from app.i18n import t
from app.schemas import MedicalFacility
from app.services.medical.medical_facility_matcher import WEEKDAY_LABELS
from resources.flex_messages import theme
from app.services.medical.business_hours import resolve_business_hours
from resources.flex_messages.medical_messages.facility_brief_flex_message import (
    _build_flex_map_uri,
    _build_flex_tel_uri,
    _build_status_indicator,
)

# 診療科別網格每列顯示筆數
DEPARTMENTS_PER_ROW = 3


def _build_clinic_time_rows(
    clinic_time: dict[str, Any] | None,
    ft: theme.FlexTheme,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """依 WEEKDAY_LABELS 順序，組出營業時間表格的每一列（含斑馬紋底色）"""
    rows: list[dict[str, Any]] = []
    if not clinic_time:
        return rows

    for idx, day_key in enumerate(WEEKDAY_LABELS):
        day = clinic_time.get(day_key)
        if day is None:
            continue

        if day.isClosed:
            time_text = t("flex.detail.day_closed", language)
        else:
            ranges = [
                f"{slot.open}-{slot.close}"
                for slot in day.slots
                if slot.open and slot.close
            ]
            time_text = "、".join(ranges) if ranges else t("flex.detail.no_data", language)

        # 斑馬紋底色，單雙數列交替
        bg_color = theme.BRAND_TINT if idx % 2 == 0 else theme.SURFACE

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
                        "text": t(f"weekday.{day_key}", language),
                        "size": ft.body,
                        "weight": "bold",
                        "color": theme.BRAND_DARK,
                        "flex": 1,
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": time_text,
                        "size": ft.body,
                        "color": theme.TEXT,
                        "flex": 3,  # flex 3不會overflow
                        "wrap": True,  # 允許自動折行，避免三段時間導致 overflow
                    },
                ],
            }
        )
    return rows


def _department_chip(name: str, ft: theme.FlexTheme) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": theme.BRAND_TINT,
        "cornerRadius": "md",
        "paddingAll": "md",  # 內距足夠，讓不可點擊的科別標籤仍厚實易讀
        "flex": 1,
        "contents": [
            {
                "type": "text",
                "text": name,
                "size": ft.caption,
                "color": theme.BRAND_DARK,
                "align": "center",
                "weight": "bold",
                "wrap": True,
            }
        ],
    }


def _build_department_grid(
    departments: list[str] | None,
    ft: theme.FlexTheme,
    language: str | None = None,
) -> list[dict[str, Any]]:
    """組出完整診療科別網格"""

    if not departments:
        return [
            {
                "type": "text",
                "text": t("flex.detail.no_data", language),
                "size": ft.body,
                "color": theme.TEXT_MUTED,
            }
        ]

    rows: list[dict[str, Any]] = []

    # 依每列顯示筆數切分
    for row_index in range(0, len(departments), DEPARTMENTS_PER_ROW):
        row_departments = departments[row_index : row_index + DEPARTMENTS_PER_ROW]

        row_chips: list[dict[str, Any]] = [
            _department_chip(name, ft) for name in row_departments
        ]

        # 若不滿一列，補上 filler 填滿空間
        while len(row_chips) < DEPARTMENTS_PER_ROW:
            row_chips.append({"type": "filler"})

        rows.append(
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "margin": "sm",
                "contents": row_chips,
            }
        )

    return rows


def generate_facility_detail_flex_message(
    facility: MedicalFacility,
    language: str | None = None,
    font_size: str | None = None,
) -> dict[str, Any]:
    """根據單一醫療院所完整資料，動態渲染詳情版 Flex Message"""
    ft = theme.resolve_theme(font_size)
    map_uri = _build_flex_map_uri(facility)
    call_label = t("flex.button.call", language)
    map_label = t("flex.button.map", language)

    header_contents: list[dict[str, Any]] = []
    if facility.type:
        header_contents.append(
            {
                "type": "text",
                "text": facility.type,
                "size": ft.caption,
                "weight": "bold",
                "color": theme.BRAND,
            }
        )
    header_contents.append(
        {
            "type": "text",
            "text": facility.name or t("flex.facility.unknown_name", language),
            "weight": "bold",
            "wrap": True,
            "size": ft.title,
            "color": theme.TEXT,
            "margin": "xs",
        }
    )
    header_contents.append(
        _build_status_indicator(resolve_business_hours(facility), ft, language)
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
            "text": facility.address or t("flex.facility.no_address", language),
            "wrap": True,
            "size": ft.body,
            "color": theme.TEXT_MUTED,
            "margin": "md",
        },
    ]

    # 電話號碼可點擊撥號；無有效電話時僅顯示純文字，不加 action
    phone_text_block: dict[str, Any] = {
        "type": "text",
        "text": facility.phone or t("flex.detail.no_data", language),
        "size": ft.body,
        "margin": "xs",
        "wrap": True,
    }
    tel_uri = _build_flex_tel_uri(facility.phone)
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

    body_contents.append(theme.divider("xl"))

    clinic_time_rows = _build_clinic_time_rows(facility.clinic_time, ft, language)
    body_contents.append(
        {
            "type": "box",
            "layout": "vertical",
            "margin": "xl",
            "contents": [
                ft.section_title(t("flex.detail.hours", language)),
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
                            "size": ft.body,
                            "color": theme.TEXT_MUTED,
                        }
                    ],
                },
            ],
        }
    )

    body_contents.append(theme.divider("xl"))

    department_count = len(facility.departments) if facility.departments else 0
    body_contents.append(
        {
            "type": "box",
            "layout": "vertical",
            "margin": "xl",
            "spacing": "sm",
            "contents": [
                ft.section_title(
                    t("flex.detail.departments", language).format(
                        count=department_count
                    )
                ),
                *_build_department_grid(facility.departments, ft, language),
            ],
        }
    )

    footer_contents: list[dict[str, Any]] = []
    if tel_uri != "tel:":
        footer_contents.append(
            ft.primary_button(
                call_label,
                {"type": "uri", "label": call_label, "uri": tel_uri},
            )
        )
    footer_contents.append(
        ft.secondary_button(
            map_label,
            {"type": "uri", "label": map_label, "uri": map_uri},
        )
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
                "paddingAll": "xl",
                "backgroundColor": theme.SURFACE,
                "contents": body_contents,
            },
            "footer": {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "paddingAll": "lg",
                "contents": footer_contents,
            },
        },
    }
