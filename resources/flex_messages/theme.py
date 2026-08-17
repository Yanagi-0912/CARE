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
    "heading": {"normal": "xl", "large": "xxl", "xlarge": "3xl"},
    "body": {"normal": "md", "large": "lg", "xlarge": "xl"},
    "caption": {"normal": "sm", "large": "md", "xlarge": "lg"},
    "button": {"normal": "lg", "large": "xl", "xlarge": "xxl"},
    # 藥丸縮圖（app/services/line_messaging/flex/medication_flex.py 的
    # _medication_row_node）：本功能的前提是靠外觀（形狀、顏色）認藥，字級調大
    # 的長輩不該仍被鎖在固定的最小縮圖上——那樣藥名變大、照片卻原地不動，等於
    # 把「放大字級＝方便閱讀」的訴求做了一半。design.md 決策 6 只定了縮圖來源
    # 檔（160px 正方形、保留比例尺）的尺寸，沒有定推播時的顯示尺寸，因此這裡
    # 沿用既有的三段字級系統，而不是另訂一個固定值。
    #
    # 起點從 sm（80px）改為 xxl：LINE 的 image size 關鍵字對應固定寬度
    # （sm=80、md=100、lg=120、xl=140、xxl=160、3xl=180、4xl=200），80px 在
    # 推播裡小到看不出藥丸的顏色與刻痕，等於這張照片不存在。xxl=160px 正好
    # 等於落地縮圖的原始解析度（scripts/build_drug_catalog.py 的
    # IMAGE_THUMBNAIL_PX），是「不模糊」的上限；large／xlarge 兩檔略為超出
    # 原始解析度是刻意的取捨——把字級調大的人多半是視力需求，寧可稍微鬆散
    # 也不要讓照片原地不動。這兩檔的鬆散程度值得在真機上看一眼再定案
    # （160px 的來源放到 200px），normal 這檔不受影響。
    "thumbnail": {"normal": "xxl", "large": "3xl", "xlarge": "4xl"},
}


@dataclass(frozen=True)
class FlexTheme:
    """一次解析好字級，供單一則 Flex Message 內所有元件共用。"""

    title: str
    heading: str
    body: str
    caption: str
    button: str
    thumbnail: str

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
