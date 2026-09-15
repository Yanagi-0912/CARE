"""貼圖回覆的分類：LINE 附上的關鍵字與訊息貼圖文字 → 回哪一句。"""

import pytest

from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n.messages import _MESSAGES
from app.services.line_messaging.sticker_reply import (
    STICKER_REPLY_KEYS,
    sticker_reply_key,
)

# LINE 官方文件的兩個貼圖事件範例
# （https://developers.line.biz/en/reference/messaging-api/#wh-sticker）
LINE_DOC_ANIMATED_KEYWORDS = [
    "cony", "sally", "Staring", "hi", "whatsup", "line", "howdy", "HEY",
    "Peeking", "wave", "peek", "Hello", "yo", "greetings",
]
LINE_DOC_MESSAGE_STICKER_KEYWORDS = [
    "Anticipation", "Sparkle", "Straight face", "Staring", "Thinking",
]


def test_line_doc_animated_example_is_a_greeting():
    assert sticker_reply_key(LINE_DOC_ANIMATED_KEYWORDS, None) == "sticker.reply.greeting"


def test_sdk_fixture_love_sticker():
    # line-bot-sdk-python 的 tests/text/webhook.json 裡唯一的貼圖事件
    assert sticker_reply_key(["Love You", "Love"], "Just sticker") == "sticker.reply.love"


def test_line_doc_message_sticker_example_falls_back():
    assert (
        sticker_reply_key(
            LINE_DOC_MESSAGE_STICKER_KEYWORDS, "Let's\nhang out\nthis weekend!"
        )
        == "sticker.reply.fallback"
    )


def test_no_keywords_falls_back():
    assert sticker_reply_key(None, None) == "sticker.reply.fallback"
    assert sticker_reply_key([], "") == "sticker.reply.fallback"


def test_english_terms_match_whole_words_only():
    # hi 不能撞到 this、Thinking；ill 不能撞到 will
    assert sticker_reply_key(["this", "Thinking", "will"], None) == "sticker.reply.fallback"


def test_message_sticker_text_outranks_keywords():
    # 使用者親手打的字比關鍵字準：關鍵字全是打招呼、文字寫謝謝，就回謝謝
    assert sticker_reply_key(LINE_DOC_ANIMATED_KEYWORDS, "謝謝你！") == "sticker.reply.thanks"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("晚安", "sticker.reply.good_night"),
        ("早安", "sticker.reply.good_morning"),
        ("我今天頭好痛", "sticker.reply.unwell"),
        ("收到", "sticker.reply.ok"),
    ],
)
def test_chinese_text_is_understood(text, expected):
    assert sticker_reply_key(None, text) == expected


def test_category_with_more_keywords_wins():
    assert sticker_reply_key(["hi", "Hello", "smile"], None) == "sticker.reply.greeting"


def test_tie_between_crying_and_laughing_asks_what_is_wrong():
    assert sticker_reply_key(["Laughing", "Crying"], None) == "sticker.reply.unwell"


@pytest.mark.parametrize("key", STICKER_REPLY_KEYS)
@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_every_sticker_reply_has_its_own_translation(key, language):
    # t() 缺語言時會靜默落回 zh-TW，外語使用者會收到一句看不懂的中文
    assert _MESSAGES[key].get(language)
