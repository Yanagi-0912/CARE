"""藥單問答與聊天回報服藥的文案：六種語言都要有，佔位符要一致。

佔位符對不上時 `.format()` 會在送出前丟 KeyError，使用者收到的是整輪失敗的
fallback，而不是少一個名字——所以這裡比對的是集合，不是有沒有值。
"""

import string

import pytest

from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n.messages import _MESSAGES

KEYS = sorted(
    key
    for key in _MESSAGES
    if key.startswith(("medq.", "medreport.", "flex.medreport."))
)


def _placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_there_are_keys_to_check():
    assert KEYS


@pytest.mark.parametrize("key", KEYS)
def test_every_language_has_its_own_text_with_the_same_placeholders(key):
    expected = _placeholders(_MESSAGES[key]["zh-TW"])
    for language in SUPPORTED_LANGUAGES:
        value = _MESSAGES[key].get(language)
        assert value and value.strip(), (key, language)
        assert _placeholders(value) == expected, (key, language)
