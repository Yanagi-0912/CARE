import pytest

from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n import t
from app.services.line_messaging import feature_videos
from app.services.line_messaging.flex.feature_video_flex import build_feature_video_flex
from resources.flex_messages.theme import _SIZE_SCALE

BASE = "https://care.example.com/"


def _bubble(msg) -> dict:
    return msg.to_dict()["contents"]


def test_video_sits_in_the_hero_of_a_single_mega_bubble():
    """LINE 只接受 hero 裡的影片，bubble 要是 kilo／mega／giga 且不能在輪播裡。"""
    bubble = _bubble(build_feature_video_flex("ai", BASE, language="zh-TW"))

    assert bubble["type"] == "bubble"
    assert bubble["size"] == "mega"
    assert bubble["hero"]["type"] == "video"


def test_video_preview_and_fallback_share_one_aspect_ratio():
    hero = _bubble(build_feature_video_flex("family", BASE, language="ja"))["hero"]

    assert hero["url"] == "https://care.example.com/api/feature-videos/ja/family.mp4"
    assert hero["previewUrl"] == "https://care.example.com/api/feature-videos/ja/family.jpg"
    assert hero["aspectRatio"] == feature_videos.ASPECT_RATIO
    alt = hero["altContent"]
    assert alt["type"] == "image"
    assert alt["url"] == hero["previewUrl"]
    assert alt["aspectRatio"] == feature_videos.ASPECT_RATIO
    # 不支援影片元件的舊版 LINE 點預覽圖就用瀏覽器開影片。
    assert alt["action"] == {"type": "uri", "uri": hero["url"]}


def test_taiwanese_users_get_the_zh_tw_video():
    hero = _bubble(build_feature_video_flex("ai", BASE, language="nan-TW"))["hero"]

    assert hero["url"].endswith("/zh-TW/ai.mp4")


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_title_names_the_page_in_the_users_language(language):
    msg = build_feature_video_flex("resources", BASE, language=language)
    texts = [c["text"] for c in _bubble(msg)["body"]["contents"]]

    assert texts[0] == msg.alt_text
    assert t("welcome.section.resources.title", language) in texts[0]
    assert texts[1] == t("feature_video.hint", language)


@pytest.mark.parametrize("font_size", ["normal", "large", "xlarge"])
def test_text_size_follows_the_font_setting(font_size):
    allowed = {sizes[font_size] for sizes in _SIZE_SCALE.values()}
    msg = build_feature_video_flex("ai", BASE, font_size=font_size)

    assert {c["size"] for c in _bubble(msg)["body"]["contents"]} <= allowed
