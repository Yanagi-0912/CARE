"""分享卡：官方帳號 QR、分享按鈕、邀請家人按鈕，另附一則可複製的加好友連結。"""

import pytest

from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n.messages import _MESSAGES, t
from resources.flex_messages.share_care_flex_message import (
    generate_share_care_flex_message,
)

BASIC_ID = "@460xmyhp"
LIFF_URL = "https://liff.line.me/2009177739-JJrPzjAn"

SHARE_KEYS = (
    "flex.share.title",
    "flex.share.desc",
    "flex.share.button",
    "flex.share.family_prompt",
    "flex.share.family_button",
    "share.link_text",
    "share.unavailable",
)


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _uris(payload: dict) -> list[str]:
    return [
        node["action"]["uri"]
        for node in _walk(payload["contents"])
        if isinstance(node.get("action"), dict) and node["action"].get("type") == "uri"
    ]


def _images(payload: dict) -> list[str]:
    return [node["url"] for node in _walk(payload["contents"]) if node.get("type") == "image"]


def _texts(payload: dict) -> list[str]:
    return [node["text"] for node in _walk(payload["contents"]) if node.get("type") == "text"]


def _card(basic_id: str = BASIC_ID, liff_url: str = LIFF_URL, language: str = "zh-TW") -> dict:
    return generate_share_care_flex_message(
        basic_id, liff_url, language=language, font_size="normal"
    )


def test_card_shares_the_official_account_and_invites_family():
    payload = _card()

    assert payload["type"] == "flex"
    assert payload["altText"] == t("flex.share.title", "zh-TW")
    assert _uris(payload) == [
        "https://line.me/R/nv/recommendOA/%40460xmyhp",
        f"{LIFF_URL}/family",
    ]
    assert _images(payload) == ["https://qr-official.line.me/sid/L/460xmyhp.png"]


def test_follow_up_text_carries_the_add_friend_link():
    assert "https://line.me/R/ti/p/%40460xmyhp" in _card()["followUpText"]


def test_family_section_is_dropped_without_liff_url():
    payload = _card(liff_url="")

    assert _uris(payload) == ["https://line.me/R/nv/recommendOA/%40460xmyhp"]
    assert t("flex.share.family_prompt", "zh-TW") not in _texts(payload)


def test_basic_id_without_at_sign_still_gets_percent_encoded_at():
    """get_bot_info 的 basicId 帶 @；哪天拿到沒帶的，網址也不能少掉 %40。"""
    payload = _card(basic_id="460xmyhp")

    assert _uris(payload)[0] == "https://line.me/R/nv/recommendOA/%40460xmyhp"
    assert _images(payload) == ["https://qr-official.line.me/sid/L/460xmyhp.png"]
    assert "https://line.me/R/ti/p/%40460xmyhp" in payload["followUpText"]


def test_card_follows_the_user_language():
    payload = _card(language="ja")

    assert payload["altText"] == t("flex.share.title", "ja")
    assert payload["altText"] != t("flex.share.title", "zh-TW")
    assert "https://line.me/R/ti/p/%40460xmyhp" in payload["followUpText"]


@pytest.mark.parametrize("key", SHARE_KEYS)
@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_share_strings_exist_in_every_language(key, language):
    # 直接查字典而不是走 t()：t() 查不到該語言會退回中文，缺翻譯就被蓋掉了。
    assert _MESSAGES[key][language].strip()


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_link_text_keeps_the_url_placeholder(language):
    assert "{url}" in _MESSAGES["share.link_text"][language]
