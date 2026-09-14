import pytest

from app.core.user_language import DEFAULT_USER_LANGUAGE, SUPPORTED_LANGUAGES
from app.services.line_messaging.flex.welcome_flex import EXAMPLE_KEYS, build_welcome_flex
from resources.flex_messages.theme import _SIZE_SCALE

LIFF = "https://liff.line.me/1234-abcd"


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _bubble(msg) -> dict:
    return msg.to_dict()["contents"]


def _texts(msg) -> list[str]:
    return [node["text"] for node in _walk(_bubble(msg)) if node.get("type") == "text"]


def _actions(msg) -> list[dict]:
    return [node["action"] for node in _walk(_bubble(msg)) if "action" in node]


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


def test_example_buttons_send_the_text_they_show():
    msg = build_welcome_flex(LIFF, language="zh-TW")
    sent = [a["text"] for a in _actions(msg) if a["type"] == "message"]

    assert len(sent) == len(EXAMPLE_KEYS)
    assert "血壓多少算高？" in sent
    assert all(text in _texts(msg) for text in sent)


def test_profile_button_opens_personal_health_page():
    uris = [a["uri"] for a in _actions(build_welcome_flex(LIFF)) if a["type"] == "uri"]

    assert uris == [f"{LIFF}/personalhealth"]


def test_missing_liff_url_drops_only_the_profile_button():
    msg = build_welcome_flex("", language="zh-TW")
    actions = _actions(msg)

    assert not [a for a in actions if a["type"] == "uri"]
    assert len([a for a in actions if a["type"] == "message"]) == len(EXAMPLE_KEYS)
    assert "CARE 提供衛教資訊，不能取代醫師診斷。緊急狀況請撥 119。" in _texts(msg)


def test_actions_carry_no_label():
    assert all("label" not in action for action in _actions(build_welcome_flex(LIFF)))


@pytest.mark.parametrize("font_size", ["normal", "large", "xlarge"])
def test_every_text_size_follows_the_font_setting(font_size):
    allowed = {sizes[font_size] for sizes in _SIZE_SCALE.values()}
    msg = build_welcome_flex(LIFF, font_size=font_size)
    sizes = [n["size"] for n in _walk(_bubble(msg)) if n.get("type") == "text"]

    assert sizes
    assert set(sizes) <= allowed
