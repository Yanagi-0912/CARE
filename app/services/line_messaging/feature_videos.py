"""功能說明卡的操作示範影片：檔案放哪裡、對外網址怎麼組。

影片由 scripts/feature_videos/render_feature_videos.py 產生，每種語言、每一頁功能各一支 mp4，
外加一張預覽圖（影片最後一格）。歡迎卡每頁的「看操作示範」按鈕送 postback，
dispatcher 再回一則帶影片的 Flex（見 flex/feature_video_flex.py）。

影片不能直接放進歡迎卡：LINE 規定含影片的 bubble 不能是輪播的一頁
（https://developers.line.biz/en/docs/messaging-api/create-flex-message-including-video/）。

掛在 /api 底下是因為 ingress 已經把 /api 整段導到 backend；另開一個前綴就得
同時改 CARE-infra 的 ingress，漏改的話會落到前端的 / 規則、拿到 index.html，
影片永遠播不出來而且不會報錯（同 /drug-appearance 那條規則旁的說明）。
"""

from __future__ import annotations

from pathlib import Path

from app.core.user_language import SUPPORTED_LANGUAGES, normalize_user_language

VIDEO_DIR = Path("resources/feature_videos")
URL_PATH = "/api/feature-videos"

# 與 welcome_flex.SECTIONS 的 key 一致（測試會檢查）。這裡另列一份，是因為
# 路由模組只需要檔名規則，不該為此載入整個 LINE SDK 與歡迎卡。
SECTION_KEYS: tuple[str, ...] = ("ai", "media", "daily", "resources", "family", "other")

# 影片與預覽圖都是 720×1080。LINE 要求 aspectRatio、影片、預覽圖三者比例一致，
# 不一致時會依裝置裁切。
ASPECT_RATIO = "2:3"
VIDEO_SIZE = (720, 1080)

VIDEO_EXT = "mp4"
PREVIEW_EXT = "jpg"


def file_path(language: str, section: str, ext: str) -> Path:
    return VIDEO_DIR / language / f"{section}.{ext}"


def video_url(base_url: str, language: str | None, section: str) -> str:
    return _url(base_url, language, section, VIDEO_EXT)


def preview_url(base_url: str, language: str | None, section: str) -> str:
    return _url(base_url, language, section, PREVIEW_EXT)


def _url(base_url: str, language: str | None, section: str, ext: str) -> str:
    # 選台語的使用者文字是 zh-TW，影片也用 zh-TW 那一份。
    lang = normalize_user_language(language)
    return f"{base_url.strip().rstrip('/')}{URL_PATH}/{lang}/{section}.{ext}"


def is_known(language: str, section: str) -> bool:
    return language in SUPPORTED_LANGUAGES and section in SECTION_KEYS
