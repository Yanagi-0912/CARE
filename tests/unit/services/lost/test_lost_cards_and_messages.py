"""走失求救的文案與卡片：六種語言都有、佔位符一致、最大字級下卡片不會被 LINE 退回。"""

import re

import pytest

from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n.messages import _MESSAGES
from app.models.family_authorization import (
    STRICT_NOTIFICATION_KINDS,
    notification_recipient_roles,
)
from resources.flex_messages.lost_location_flex_message import (
    MAX_QUOTED_CHARS,
    build_elder_share_bubble,
    build_family_alert_bubble,
    build_family_notice_bubble,
    build_no_family_flex,
)
from resources.flex_messages.size_guard import fits

KEYS = sorted(key for key in _MESSAGES if key.startswith("lost."))
_PLACEHOLDER = re.compile(r"{(\w+)}")
_HAN = re.compile(r"[一-鿿]")


def test_lost_messages_exist():
    assert len(KEYS) >= 30


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


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_quick_reply_label_fits_lines_20_character_limit(language):
    assert len(_MESSAGES["lost.elder.quick_reply"][language]) <= 20


def test_only_guardian_and_caregiver_receive_lost_alerts():
    assert notification_recipient_roles("elder_lost") == frozenset({"GUARDIAN", "CAREGIVER"})
    # 還沒指派角色的家庭（影子模式）要送族譜全員，不能一個都收不到
    assert "elder_lost" not in STRICT_NOTIFICATION_KINDS


LONG_NAME = "王" * 40
LONG_WORDS = "我迷路了" * 100
WATCH = "https://liff.line.me/1234-abcd/lost/watch?user=U" + "0" * 32
MAPS = "https://www.google.com/maps/dir/?api=1&destination=25.033964,121.564468"


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("font_size", ["normal", "xlarge"])
@pytest.mark.parametrize("intent", ["lost", "share"])
def test_family_alert_fits_even_with_long_words(language, font_size, intent):
    bubble = build_family_alert_bubble(
        intent=intent,
        patient_name=LONG_NAME,
        patient_words=LONG_WORDS,
        watch_url=WATCH,
        language=language,
        font_size=font_size,
    )
    assert fits(bubble)
    assert LONG_WORDS[:MAX_QUOTED_CHARS] + "…" in str(bubble)


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("font_size", ["normal", "xlarge"])
def test_other_cards_fit(language, font_size):
    assert fits(
        build_elder_share_bubble(
            header_key="lost.elder.reopen.header",
            body_key="lost.elder.reopen.body",
            share_url="https://liff.line.me/1234-abcd/lost/share",
            language=language,
            font_size=font_size,
        )
    )
    assert fits(
        build_family_notice_bubble(
            title=_MESSAGES["lost.family.stale.alt_text"][language].format(name=LONG_NAME),
            body_text=_MESSAGES["lost.family.stale.body"][language].format(minutes=12),
            watch_url=WATCH,
            navigate_url=MAPS,
            language=language,
            font_size=font_size,
        )
    )
    assert fits(build_no_family_flex(language=language, font_size=font_size).contents.to_dict())


def test_uri_action_labels_stay_within_20_characters():
    bubble = build_family_notice_bubble(
        title="t", body_text="b", watch_url=WATCH, navigate_url=MAPS, language="id"
    )
    labels = re.findall(r"'label': '([^']*)'", str(bubble))
    assert labels and all(len(label) <= 20 for label in labels)
