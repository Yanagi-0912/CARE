"""
Flex Message 共用設計 token。

色彩固定，字級則依使用者的 UserSettings.font_size 動態解析
（normal / large / xlarge），預設 large 以符合長輩取向。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.user_font_size import get_request_font_size, normalize_user_font_size

# 主色
BRAND = "#2E7D32"
BRAND_DARK = "#1B5E20"
BRAND_TINT = "#EDF5EE"

# 文字
TEXT = "#111111"
TEXT_MUTED = "#555555"
TEXT_FAINT = "#777777"
TEXT_ON_BRAND = "#FFFFFF"

# 介面
SURFACE = "#FFFFFF"
SURFACE_ALT = "#F4F6F4"
BORDER = "#DDDDDD"

# 狀態
STATUS_OPEN = "#2E7D32"
STATUS_CLOSED = "#C62828"
STATUS_UNKNOWN = "#757575"
# 午休中／請電洽：屬「今天還有機會」或「要先確認」，用琥珀色與紅色的休診區隔
STATUS_PENDING = "#E65100"
# 設有急診：不是營業狀態而是能力標示，用藍色避免與綠色的「營業中」混淆
STATUS_EMERGENCY = "#1565C0"

# 次要按鈕
NEUTRAL_BG = "#ECEFEC"

# 各語義角色在三種字級設定下對應的 LINE Flex size keyword
_SIZE_SCALE: dict[str, dict[str, str]] = {
    "title": {"normal": "xl", "large": "3xl", "xlarge": "4xl"},
    "heading": {"normal": "lg", "large": "xl", "xlarge": "xxl"},
    "body": {"normal": "md", "large": "lg", "xlarge": "xl"},
    "caption": {"normal": "sm", "large": "md", "xlarge": "lg"},
    "button": {"normal": "lg", "large": "xl", "xlarge": "xxl"},
}


@dataclass(frozen=True)
class FlexTheme:
    """一次解析好字級，供單一則 Flex Message 內所有元件共用。"""

    title: str
    heading: str
    body: str
    caption: str
    button: str

    def primary_button(self, label: str, action: dict[str, Any]) -> dict[str, Any]:
        """實心主要按鈕，內距放大以擴大點擊判定面積。"""
        return self._button_box(label, action, BRAND, TEXT_ON_BRAND)

    def secondary_button(self, label: str, action: dict[str, Any]) -> dict[str, Any]:
        """淺底次要按鈕。"""
        return self._button_box(label, action, NEUTRAL_BG, TEXT)

    def _button_box(
        self, label: str, action: dict[str, Any], background: str, color: str
    ) -> dict[str, Any]:
        return {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": background,
            "cornerRadius": "md",
            "paddingAll": "lg",
            "flex": 1,
            "action": action,
            "contents": [
                {
                    "type": "text",
                    "text": label,
                    "color": color,
                    "weight": "bold",
                    "size": self.button,
                    "align": "center",
                    "wrap": True,
                }
            ],
        }

    def section_title(self, text: str) -> dict[str, Any]:
        return {
            "type": "text",
            "text": text,
            "weight": "bold",
            "size": self.heading,
            "color": TEXT,
        }


def resolve_theme(font_size: str | None = None) -> FlexTheme:
    """
    依 font_size 解析出該使用者的字級組合。
    font_size 為 None 時讀取 request-scoped ContextVar。
    """
    scale = (
        get_request_font_size()
        if font_size is None
        else normalize_user_font_size(font_size)
    )
    return FlexTheme(**{role: sizes[scale] for role, sizes in _SIZE_SCALE.items()})


def divider(margin: str = "lg") -> dict[str, Any]:
    return {"type": "separator", "margin": margin, "color": BORDER}
