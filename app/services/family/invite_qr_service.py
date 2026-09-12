"""家庭邀請的 QR 圖片。

這支服務只負責「把邀請碼變成一張 PNG」，邀請是否還有效由呼叫端（路由）
先向 `FamilyTreeService` 問清楚。

QR 內容用 LIFF URL（`https://liff.line.me/{LIFF_ID}/join?code=...`）而不是
站台網址：LINE 內建掃描器與手機系統相機掃到 `liff.line.me` 都會喚起 LINE、
在 LIFF 內開啟，登入狀態是現成的；掃到站台網址則會用系統瀏覽器開，得再跑
一次完整的 LINE OAuth。前端不必為此改路由——`CARE-LIFF/src/utils/liffState.ts`
已經會把 `?liff.state=/join?code=...` 還原成 `/join?code=...`。
"""

import logging
import re
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Optional

import segno
from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)

# 邀請碼由 `secrets.token_urlsafe()` 產生，字元集是 base64url。這裡不綁死
# 長度，是為了讓 `create_invitation` 日後調整 nbytes 時不必同步改這裡；
# 光是形狀就足以擋掉路徑穿越與任何不是邀請碼的輸入。
INVITE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

EXPIRED_IMAGE_PATH = (
    Path(__file__).resolve().parents[3] / "resources" / "invite_qr_expired.png"
)

# 與 resources/invite_qr_expired.png 同尺寸，Flex 卡片在有效／失效之間切換時
# 版面不會跳動。Flex 的 image component 上限是 1024x1024。
QR_PIXEL_SIZE = 640

# 取自 CARE-LIFF 的主題變數（--ink / --bg）。兩者對比約 18:1，離掃描器需要的
# 門檻還很遠，配色不會犧牲辨識率。
QR_DARK = "#1c1a15"
QR_LIGHT = "#faf8f3"

# QR 規格要求的靜空區是 4 個模組寬；少於這個寬度掃描器可能找不到定位圖案。
QR_BORDER_MODULES = 4

# 錯誤修正等級 M（約可容忍 15% 汙損，L 只有 7%）。當面掃碼會遇到螢幕反光與
# 指紋，L 太脆；邀請 URL 只有 60 字元上下，升到 M 不會讓模組密到難掃。
QR_ERROR_LEVEL = "m"


# QR 圖片端點的對外路徑。`/api/family` 這段由 app/main.py 掛載 family_tree_router
# 時決定，改那裡要一併改這裡。
INVITE_QR_URL_TEMPLATE = "/api/family/invites/{code}/qr.png"


def build_invite_qr_url(code: str) -> Optional[str]:
    """QR 圖片的絕對網址，供前端顯示與 Flex Message 的 image component 使用。

    必須是絕對網址：Flex 的圖片是由 LINE 的伺服器去抓的，相對路徑對它沒有
    意義。沿用 `PUBLIC_BASE_URL`——藥丸縮圖組 LINE 可讀網址時用的是同一個。

    未設定時回 None 而不是拋錯：邀請本身（連結那條路）不該因為 QR 生不出
    網址就整個失敗，前端收到 None 就把 QR 區塊藏起來。
    """
    base = settings.PUBLIC_BASE_URL.strip().rstrip("/")
    if not base:
        logger.warning("PUBLIC_BASE_URL 未設定，邀請 QR 無法組出對外網址")
        return None
    return base + INVITE_QR_URL_TEMPLATE.format(code=code)


def build_invite_url(code: str) -> Optional[str]:
    """掃描 QR 或點擊連結後要開啟的網址。LIFF_ID 未設定時回 None。

    QR 與連結共用這一支，兩者就不可能指向不同的地方。
    """
    liff_id = settings.LIFF_ID.strip()
    if not liff_id:
        logger.warning("LIFF_ID 未設定，無法組出邀請的 LIFF 網址")
        return None
    return f"https://liff.line.me/{liff_id}/join?code={code}"


def render_invite_qr_png(code: str) -> bytes:
    """把邀請碼畫成 PNG。"""
    url = build_invite_url(code)
    if url is None:
        # 這裡不能降級。悄悄產出一張指向 `liff.line.me//join` 的壞 QR，會變成
        # 「掃了沒反應」的鬼故事，比直接失敗難查得多。
        logger.error("LIFF_ID 未設定，無法產生邀請 QR")
        raise HTTPException(status_code=500, detail="LIFF_ID is not configured")

    qr = segno.make(url, error=QR_ERROR_LEVEL)

    # 模組數會隨 URL 長度變動（LIFF ID 長短、邀請碼長度），固定 scale 會讓
    # 圖片尺寸跟著飄。反過來由目標像素寬推回 scale，輸出尺寸才穩定。
    modules_wide, _ = qr.symbol_size(scale=1, border=QR_BORDER_MODULES)
    scale = max(1, round(QR_PIXEL_SIZE / modules_wide))

    buffer = BytesIO()
    qr.save(
        buffer,
        kind="png",
        scale=scale,
        border=QR_BORDER_MODULES,
        dark=QR_DARK,
        light=QR_LIGHT,
    )
    return buffer.getvalue()


@lru_cache(maxsize=1)
def expired_invite_png() -> bytes:
    """「此邀請已失效」的替代圖。

    內容永遠不變，讀一次就夠。產生方式見 scripts/build_invite_expired_image.py
    ——正式映像（python:3.12-slim）沒有中文字型，這張圖不能在 runtime 畫。
    """
    return EXPIRED_IMAGE_PATH.read_bytes()
