import json
import re

import pytest

from app.core.user_language import DEFAULT_USER_LANGUAGE, SUPPORTED_LANGUAGES
from app.i18n import t
from app.services.line_messaging.feature_guide_intent import is_feature_guide_intent
from app.services.line_messaging.flex.welcome_flex import (
    EXAMPLE_KEYS,
    SECTIONS,
    build_welcome_flex,
    feature_video_postback,
)
from resources.flex_messages.theme import _SIZE_SCALE

LIFF = "https://liff.line.me/1234-abcd"

# LINE 輪播的上限：最多 12 頁、整則 JSON 不超過 50 KB。
MAX_BUBBLES = 12
MAX_CAROUSEL_BYTES = 50_000


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _carousel(msg) -> dict:
    return msg.to_dict()["contents"]


def _texts(msg) -> list[str]:
    return [node["text"] for node in _walk(_carousel(msg)) if node.get("type") == "text"]


def _actions(msg) -> list[dict]:
    return [node["action"] for node in _walk(_carousel(msg)) if "action" in node]


def test_one_page_for_welcome_then_one_per_section():
    carousel = _carousel(build_welcome_flex(LIFF, language="zh-TW"))
    headers = [bubble["header"]["contents"][0]["text"] for bubble in carousel["contents"]]

    assert carousel["type"] == "carousel"
    assert headers == [t("welcome.title", "zh-TW")] + [
        t(f"welcome.section.{section.key}.title", "zh-TW") for section in SECTIONS
    ]


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("font_size", ["normal", "xlarge"])
def test_fits_line_carousel_limits(language, font_size):
    carousel = _carousel(
        build_welcome_flex(LIFF, language=language, font_size=font_size, with_videos=True)
    )

    assert len(carousel["contents"]) <= MAX_BUBBLES
    assert len(json.dumps(carousel, ensure_ascii=False).encode()) <= MAX_CAROUSEL_BYTES


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_every_text_is_translated(language):
    """t() 查不到 key 會回傳 key 本身、缺某語言會退回中文——兩種都不能出現在卡片上。"""
    msg = build_welcome_flex(LIFF, language=language)
    texts = _texts(msg)

    assert texts
    assert not [text for text in texts if text.startswith("welcome.")]
    if language != DEFAULT_USER_LANGUAGE:
        zh_texts = set(_texts(build_welcome_flex(LIFF, language=DEFAULT_USER_LANGUAGE)))
        assert not set(texts) & zh_texts


def test_every_feature_has_a_name_and_how_to_use_it():
    texts = _texts(build_welcome_flex(LIFF, language="zh-TW"))

    for section in SECTIONS:
        for name in section.features:
            assert t(f"welcome.feature.{name}.name", "zh-TW") in texts
            assert t(f"welcome.feature.{name}.how", "zh-TW") in texts


def test_example_buttons_send_the_text_they_show():
    msg = build_welcome_flex(LIFF, language="zh-TW")
    sent = [a["text"] for a in _actions(msg) if a["type"] == "message"]

    assert sent == [t(key, "zh-TW") for key in EXAMPLE_KEYS]
    assert "血壓多少算高？" in sent
    assert all(text in _texts(msg) for text in sent)


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_no_button_sends_a_phrase_with_side_effects(language):
    """走失、回報服藥、回報知識問題一送出就會通知家人、記紀錄、送單，只能寫在說明裡。"""
    sent = {
        a["text"]
        for a in _actions(build_welcome_flex(LIFF, language=language))
        if a["type"] == "message"
    }
    side_effect_how = [
        t(f"welcome.feature.{name}.how", language) for name in ("lost", "med_report", "kb_report")
    ]

    assert sent
    for text in sent:
        assert not any(text in how for how in side_effect_how)


def test_video_buttons_only_when_videos_can_be_served():
    without = [a for a in _actions(build_welcome_flex(LIFF)) if a["type"] == "postback"]
    with_videos = [
        a
        for a in _actions(build_welcome_flex(LIFF, language="en", with_videos=True))
        if a["type"] == "postback"
    ]

    assert without == []
    assert [a["data"] for a in with_videos] == [feature_video_postback(s.key) for s in SECTIONS]
    assert {a["displayText"] for a in with_videos} == {t("welcome.video_display", "en")}


def test_profile_button_opens_personal_health_page():
    uris = [a["uri"] for a in _actions(build_welcome_flex(LIFF)) if a["type"] == "uri"]

    assert uris == [f"{LIFF}/personalhealth"]


def test_missing_liff_url_drops_only_the_profile_button():
    msg = build_welcome_flex("", language="zh-TW")
    actions = _actions(msg)

    assert not [a for a in actions if a["type"] == "uri"]
    assert len([a for a in actions if a["type"] == "message"]) == len(EXAMPLE_KEYS)
    assert "CARE 提供衛教資訊，不能取代醫師診斷。緊急狀況請撥 119。" in _texts(msg)


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("with_videos", [False, True])
def test_actions_have_labels_within_line_limit(language, with_videos):
    msg = build_welcome_flex(LIFF, language=language, with_videos=with_videos)
    buttons = [node for node in _walk(_carousel(msg)) if "action" in node]

    assert buttons
    for button in buttons:
        label = button["action"]["label"]
        visible_text = button["contents"][0]["text"]
        assert label
        assert len(label.encode("utf-16-le")) // 2 <= 40
        assert visible_text.startswith(label)
        if button["action"]["type"] == "message":
            assert button["action"]["text"] == visible_text


def test_label_limit_does_not_truncate_the_example_or_split_an_emoji(monkeypatch):
    example = "a" + "😀" * 21

    def translated(key, language=None):
        return example if key == "welcome.example.health" else t(key, language)

    monkeypatch.setattr(
        "app.services.line_messaging.flex.welcome_flex.t", translated
    )
    msg = build_welcome_flex(LIFF, language="zh-TW")
    action = next(a for a in _actions(msg) if a["type"] == "message")

    assert action["label"] == "a" + "😀" * 19
    assert action["text"] == example
    assert example in _texts(msg)


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_quick_reply_opens_camera_camera_roll_and_feature_guide(language):
    items = build_welcome_flex(LIFF, language=language).to_dict()["quickReply"]["items"]
    actions = [item["action"] for item in items]

    assert [a["type"] for a in actions] == ["camera", "cameraRoll", "message"]
    assert actions[-1]["text"] == t("welcome.quick.guide", language)
    assert is_feature_guide_intent(actions[-1]["text"])
    # LINE 快速回覆的 label 必填，上限 20 字。
    assert all(0 < len(a["label"]) <= 20 for a in actions)


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_recall_hint_names_a_phrase_that_brings_the_card_back(language):
    hint = t("welcome.recall_hint", language)
    quoted = re.findall(r"[「“](.+?)[」”]", hint)

    assert len(quoted) == 1
    assert quoted[0] == t("welcome.quick.guide", language)
    assert is_feature_guide_intent(quoted[0])


@pytest.mark.parametrize("font_size", ["normal", "large", "xlarge"])
def test_every_text_size_follows_the_font_setting(font_size):
    allowed = {sizes[font_size] for sizes in _SIZE_SCALE.values()}
    msg = build_welcome_flex(LIFF, font_size=font_size)
    sizes = [n["size"] for n in _walk(_carousel(msg)) if n.get("type") == "text"]

    assert sizes
    assert set(sizes) <= allowed
