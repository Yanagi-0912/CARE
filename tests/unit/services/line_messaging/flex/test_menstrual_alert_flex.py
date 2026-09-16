"""經期異常推播卡（health-alerts spec「經期異常只通知本人」）：文字
SHALL NOT 含「經期」「月經」（或其他五語系的對應詞）或任何數字，且在最大
字級下仍不超過 LINE 的大小上限。
"""

import re

import pytest

from app.i18n.messages import _MESSAGES
from app.services.line_messaging.flex.menstrual_alert_flex import (
    build_menstrual_alert_flex,
    menstrual_alert_alt_text,
)
from resources.flex_messages.size_guard import fits

LANGUAGES = ("zh-TW", "en", "id", "vi", "th", "ja")
FONT_SIZES = ("normal", "large", "xlarge")
MENSTRUAL_ALERT_KEYS = [k for k in _MESSAGES if k.startswith("flex.menstrual_alert.")]

# 各語系「經期」「月經」的對應詞（比對用小寫）。
_FORBIDDEN_WORDS = {
    "zh-TW": ("經期", "月經"),
    "en": ("period", "menstru"),
    "id": ("menstruasi", "haid"),
    "vi": ("kinh nguyệt", "kinh nguyet"),
    "th": ("ประจำเดือน",),
    "ja": ("生理", "月経"),
}


def _collect_text_nodes(node) -> list:
    """遞迴取出 Flex 節點樹裡所有可見的 ``text`` 內容。

    刻意不對整份 dict 字串化後檢查——版面本身（顏色十六進位碼、layout
    關鍵字、按鈕的 ``uri``）含有數字與英文字是正常的，那些不是「推播
    文字」；只有 ``type: text`` 節點的 ``text`` 才是使用者真正會讀到的字。
    """
    texts: list = []
    if isinstance(node, dict):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            texts.append(node["text"])
        for value in node.values():
            texts.extend(_collect_text_nodes(value))
    elif isinstance(node, list):
        for item in node:
            texts.extend(_collect_text_nodes(item))
    return texts


@pytest.mark.parametrize("language", LANGUAGES)
def test_card_text_has_no_menstrual_category_words_or_digits(language):
    flex = build_menstrual_alert_flex(
        button_uri="https://liff.line.me/1234/health-records", language=language
    )
    visible_text = "\n".join(_collect_text_nodes(flex.contents.to_dict()))
    lowered = visible_text.lower()

    for forbidden in _FORBIDDEN_WORDS[language]:
        assert forbidden.lower() not in lowered
    assert not re.search(r"\d", visible_text)
    assert not re.search(r"\d", flex.alt_text)


@pytest.mark.parametrize("language", LANGUAGES)
def test_alt_text_has_no_menstrual_category_words_or_digits(language):
    text = menstrual_alert_alt_text(language)
    lowered = text.lower()

    for forbidden in _FORBIDDEN_WORDS[language]:
        assert forbidden.lower() not in lowered
    assert not re.search(r"\d", text)


def test_button_omitted_when_no_uri_given():
    flex = build_menstrual_alert_flex(language="zh-TW", button_uri="")
    assert "footer" not in flex.contents.to_dict()


def test_button_uri_present_when_given():
    flex = build_menstrual_alert_flex(
        language="zh-TW", button_uri="https://liff.line.me/1234/health-records"
    )
    assert "https://liff.line.me/1234/health-records" in str(flex.contents.to_dict())


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("font_size", FONT_SIZES)
def test_card_fits_size_guard_at_every_font_size(language, font_size):
    flex = build_menstrual_alert_flex(
        button_uri="https://liff.line.me/1234/health-records",
        language=language,
        font_size=font_size,
    )
    assert fits(flex.contents.to_dict())


@pytest.mark.parametrize("key", MENSTRUAL_ALERT_KEYS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_every_menstrual_alert_key_has_every_language(key, language):
    assert _MESSAGES[key].get(language)
