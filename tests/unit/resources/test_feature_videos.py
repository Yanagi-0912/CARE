"""守住已提交的功能說明示範影片（scripts/feature_videos/render_feature_videos.py 產生）。

少了某一支時，使用者按「看操作示範」只會看到一個播不出來的影片框，線上
沒有任何錯誤——LINE 是自己來抓網址的。所以每種語言、每一頁都要有 mp4 與預覽圖。
"""

import pytest
from PIL import Image

from app.core.user_language import SUPPORTED_LANGUAGES
from app.services.line_messaging import feature_videos
from app.services.line_messaging.flex.welcome_flex import SECTIONS

# 實測每支 10 秒左右、180–430 KB。超過 2 MB 多半是 CRF 或尺寸設錯，這批檔案
# 會跟著進 git 與 backend 映像，不該悄悄變大。
MAX_VIDEO_BYTES = 2_000_000
# LINE 對預覽圖的上限是 1 MB。
MAX_PREVIEW_BYTES = 1_000_000

PAIRS = [(lang, section) for lang in SUPPORTED_LANGUAGES for section in feature_videos.SECTION_KEYS]


def test_section_keys_match_the_welcome_card():
    assert feature_videos.SECTION_KEYS == tuple(section.key for section in SECTIONS)


@pytest.mark.parametrize(("language", "section"), PAIRS)
def test_every_page_has_a_streamable_video(language, section):
    path = feature_videos.file_path(language, section, feature_videos.VIDEO_EXT)
    data = path.read_bytes()

    assert 0 < len(data) <= MAX_VIDEO_BYTES
    assert data[4:8] == b"ftyp"
    # moov 在 mdat 之前（ffmpeg 的 +faststart），播放器不必下載完整檔案才能開始播。
    assert data.index(b"moov") < data.index(b"mdat")


@pytest.mark.parametrize(("language", "section"), PAIRS)
def test_every_page_has_a_preview_matching_the_video_size(language, section):
    path = feature_videos.file_path(language, section, feature_videos.PREVIEW_EXT)

    assert path.stat().st_size <= MAX_PREVIEW_BYTES
    with Image.open(path) as image:
        assert image.format == "JPEG"
        assert image.size == feature_videos.VIDEO_SIZE
