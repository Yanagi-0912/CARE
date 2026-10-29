"""產生歡迎卡「看操作示範」的影片：每種語言、每一頁功能各一支 mp4 和一張預覽圖。

畫面是 chat.html 畫的模擬聊天室，劇本在 scenes.py。每一格都由時間算出畫面再
截圖，整串 PNG 直接送進 ffmpeg 編成 H.264，所以重跑得到的影片每格都相同。

產物放在 resources/feature_videos/{語言}/{頁}.mp4／.jpg，提交進 git：正式映像
沒有瀏覽器也沒有中文、泰文字型，跟 rich_menu_*.png 一樣在開發機上產好。
網址與檔名規則見 app/services/line_messaging/feature_videos.py。

用法（需要 Playwright 與 ffmpeg；backend 的依賴裡沒有 Playwright，另外裝即可）：
    python scripts/feature_videos/render_feature_videos.py              # 全部
    python scripts/feature_videos/render_feature_videos.py --lang zh-TW --section ai
Playwright 版本與已下載的瀏覽器對不上時，用 --chromium 指定一個現成的 Chrome。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from app.core.user_language import SUPPORTED_LANGUAGES  # noqa: E402
from app.services.line_messaging import feature_videos  # noqa: E402
from scripts.feature_videos.scenes import build_scene  # noqa: E402

TEMPLATE = Path(__file__).with_name("chat.html")
FPS = 30
# chat.html 以 360×540 的 CSS 像素排版，2 倍解析度截圖剛好是 720×1080。
CSS_SIZE = (360, 540)
DEVICE_SCALE = feature_videos.VIDEO_SIZE[0] // CSS_SIZE[0]
# libx264 的品質參數：數字越小越清楚、檔案越大。介面是大片純色，22 已看不出
# 壓縮痕跡，一支 10 秒左右的影片約幾百 KB。
CRF = 22


def render_one(page, lang: str, section: str) -> tuple[Path, Path]:
    scene = build_scene(lang, section)
    page.evaluate("scene => { window.SCENE = scene; build(); }", scene)

    out_dir = ROOT / feature_videos.VIDEO_DIR / lang
    out_dir.mkdir(parents=True, exist_ok=True)
    video = out_dir / f"{section}.{feature_videos.VIDEO_EXT}"
    preview = out_dir / f"{section}.{feature_videos.PREVIEW_EXT}"

    ffmpeg = subprocess.Popen(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "png", "-i", "-",
            "-c:v", "libx264", "-preset", "slow", "-crf", str(CRF),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
            str(video),
        ],
        stdin=subprocess.PIPE,
    )
    frames = round(scene["duration"] * FPS)
    for i in range(frames):
        page.evaluate("t => render(t)", i / FPS)
        ffmpeg.stdin.write(page.screenshot(type="png"))
    ffmpeg.stdin.close()
    if ffmpeg.wait() != 0:
        raise SystemExit(f"ffmpeg 失敗：{lang}/{section}")

    # 預覽圖用最後一格：LINE 沒自動播放時先看到的就是做完之後的樣子。
    page.evaluate("t => render(t)", scene["duration"])
    page.screenshot(path=str(preview), type="jpeg", quality=85)
    return video, preview


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lang", choices=SUPPORTED_LANGUAGES, action="append")
    parser.add_argument("--section", choices=feature_videos.SECTION_KEYS, action="append")
    parser.add_argument("--chromium", help="Chrome／Chromium 執行檔路徑（預設用 Playwright 自帶的）")
    args = parser.parse_args()
    languages = args.lang or list(SUPPORTED_LANGUAGES)
    sections = args.section or list(feature_videos.SECTION_KEYS)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chromium)
        page = browser.new_page(
            viewport={"width": CSS_SIZE[0], "height": CSS_SIZE[1]},
            device_scale_factor=DEVICE_SCALE,
        )
        page.goto(TEMPLATE.as_uri())
        for lang in languages:
            for section in sections:
                started = time.perf_counter()
                # 每段都重新載入版面，前一段的狀態（輸入框、浮層）不會殘留。
                page.reload()
                video, preview = render_one(page, lang, section)
                print(
                    json.dumps(
                        {
                            "lang": lang,
                            "section": section,
                            "mp4_kb": round(video.stat().st_size / 1024),
                            "jpg_kb": round(preview.stat().st_size / 1024),
                            "seconds": round(time.perf_counter() - started, 1),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        browser.close()


if __name__ == "__main__":
    main()
