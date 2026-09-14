"""查服藥狀況的回覆文案。

這些字串直通給使用者、不經模型改寫，所以六種語言都要有自己的版本，佔位符也
要與中文原文一致——少一個佔位符，format 就會在使用者面前丟 KeyError。
"""

import re

import pytest

from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n.messages import _MESSAGES

KEYS = sorted(key for key in _MESSAGES if key.startswith("medstatus."))
_PLACEHOLDER = re.compile(r"{(\w+)}")
_HAN = re.compile(r"[一-鿿]")


def test_medication_status_messages_exist():
    assert len(KEYS) >= 20


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_every_language_has_its_own_text(key, language):
    assert _MESSAGES[key].get(language, "").strip()


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_placeholders_match_the_chinese_original(key, language):
    expected = set(_PLACEHOLDER.findall(_MESSAGES[key]["zh-TW"]))
    assert set(_PLACEHOLDER.findall(_MESSAGES[key][language])) == expected


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("language", ["en", "id", "vi", "th"])
def test_non_cjk_languages_are_not_left_in_chinese(key, language):
    assert not _HAN.search(_MESSAGES[key][language])
