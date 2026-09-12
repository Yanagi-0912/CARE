"""產生「邀請已失效」的替代圖，供 GET /api/family/invites/{code}/qr.png 回傳。

為什麼要預先產好並提交，而不是在 runtime 畫：正式映像是 python:3.12-slim，
裡面沒有任何中文字型。用 Pillow 的預設點陣字型畫「此邀請已失效」只會得到
一排豆腐格；要在 runtime 畫就得把一份 CJK 字型（數 MB）塞進映像。這張圖的
內容永遠不變，預先算好是唯一划算的作法——rich_menu_*.png 也是同樣的處理。

用法（在有 CJK 字型的機器上執行，產物提交進 git）：
    python scripts/build_invite_expired_image.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUTPUT = Path(__file__).resolve().parents[1] / "resources" / "invite_qr_expired.png"

# 與 QR 圖同尺寸，Flex 卡片在有效／失效之間切換時版面不會跳動。
SIZE = 640

# 取自 CARE-LIFF 的主題變數（--bg / --ink），讓這張圖在 LIFF dialog 裡
# 與周圍底色一致，不會看起來像破圖。
BG = "#faf8f3"
INK = "#1c1a15"
MUTED = "#8c8578"
FRAME = "#ded8c9"

# 依序尋找可用的 CJK 字型。找不到就直接失敗——這支腳本的產物是要提交的，
# 悄悄退回預設字型會產出一張全是豆腐格的圖，比失敗更糟。
FONT_CANDIDATES = [
    # macOS。PingFang 在近期系統上被搬進 AssetsV2 的雜湊路徑（不穩定），
    # 這兩個是仍留在 /System/Library/Fonts 的穩定 CJK 字型。
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),  # Debian/Ubuntu
    ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", 0),
    ("C:/Windows/Fonts/msjh.ttc", 0),  # Windows 微軟正黑體
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path, index in FONT_CANDIDATES:
        if Path(path).is_file():
            return ImageFont.truetype(path, size=size, index=index)
    raise SystemExit(
        "找不到可用的 CJK 字型，請在有中文字型的機器上執行，"
        f"或把字型路徑加進 FONT_CANDIDATES：{[p for p, _ in FONT_CANDIDATES]}"
    )


def draw_centered(draw: ImageDraw.ImageDraw, y: int, text: str, font, fill: str) -> None:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(((SIZE - (right - left)) / 2 - left, y - top), text, font=font, fill=fill)


def main() -> None:
    image = Image.new("RGB", (SIZE, SIZE), BG)
    draw = ImageDraw.Draw(image)

    # 一個空的 QR 外框，讓人一眼看出「這裡本來應該是條碼」。
    # 框不置中：底下要留兩行字，垂直置中會把字擠出畫布。
    left, top, right, bottom = 120, 72, SIZE - 120, 472
    draw.rounded_rectangle([left, top, right, bottom], radius=40, outline=FRAME, width=6)

    # 框內畫一個大叉，端點收在框內側避免壓到圓角。
    pad = 90
    draw.line([(left + pad, top + pad), (right - pad, bottom - pad)], fill=MUTED, width=18)
    draw.line([(right - pad, top + pad), (left + pad, bottom - pad)], fill=MUTED, width=18)

    draw_centered(draw, 508, "此邀請已失效", load_font(46), INK)
    draw_centered(draw, 578, "Invite expired", load_font(28), MUTED)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT, format="PNG", optimize=True)
    print(f"已寫入 {OUTPUT}（{OUTPUT.stat().st_size:,} bytes）")


if __name__ == "__main__":
    main()
