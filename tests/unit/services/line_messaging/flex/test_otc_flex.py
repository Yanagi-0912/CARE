"""非處方藥通知卡的版面與隱私邊界。"""

import re

import pytest

from app.i18n.messages import _MESSAGES
from app.services.line_messaging.flex.otc_flex import build_otc_family_flex
from resources.flex_messages.size_guard import fits

LANGUAGES = ("zh-TW", "en", "id", "vi", "th", "ja")
OTC_KEYS = [k for k in _MESSAGES if k.startswith(("flex.otc.", "text.otc."))]


def _flex(**kwargs):
    base = dict(patient_name="王大明", drug_name="普拿疼", language="zh-TW")
    return build_otc_family_flex(**{**base, **kwargs})


def test_overlap_variant_shows_both_drugs_and_the_shared_ingredient():
    flex = _flex(
        existing_drug_name="斯斯感冒膠囊", shared_ingredients=("ACETAMINOPHEN",)
    )
    rendered = str(flex.contents.to_dict())

    assert "普拿疼" in rendered
    assert "斯斯感冒膠囊" in rendered
    assert "ACETAMINOPHEN" in rendered
    assert "用藥重複提醒" in flex.alt_text


def test_added_variant_omits_the_overlap_rows():
    rendered = str(_flex().contents.to_dict())

    assert "相同成分" not in rendered
    assert "已經在吃" not in rendered


def test_shared_ingredients_alone_do_not_make_it_an_overlap_card():
    """只有成分、沒有對照的那盒藥，訊息不成立——不得渲染成重複版。"""
    flex = _flex(shared_ingredients=("ACETAMINOPHEN",))

    assert "新增了用藥提醒" in flex.alt_text
    assert "相同成分" not in str(flex.contents.to_dict())


def test_alt_text_carries_neither_drug_name_nor_indication():
    """altText 就是通知列與鎖定畫面上那一行，可能被非預期的人看到。"""
    flex = _flex(
        indication="退燒、止痛",
        existing_drug_name="斯斯感冒膠囊",
        shared_ingredients=("ACETAMINOPHEN",),
    )

    assert "普拿疼" not in flex.alt_text
    assert "斯斯感冒膠囊" not in flex.alt_text
    assert "退燒" not in flex.alt_text
    assert "ACETAMINOPHEN" not in flex.alt_text
    assert "王大明" in flex.alt_text


def test_indication_is_omitted_when_absent_rather_than_shown_empty():
    rendered = str(_flex(indication=None).contents.to_dict())

    assert "用途" not in rendered


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("font_size", ["small", "medium", "large"])
def test_card_stays_within_the_size_limit_for_every_language_and_font(language, font_size):
    flex = build_otc_family_flex(
        patient_name="王大明" * 12,
        drug_name="普拿疼加強錠膜衣錠" * 10,
        indication="退燒、止痛、緩解感冒引起的不適" * 12,
        existing_drug_name="斯斯感冒膠囊" * 10,
        shared_ingredients=("ACETAMINOPHEN", "CHLORPHENIRAMINE MALEATE", "CAFFEINE"),
        language=language,
        font_size=font_size,
    )

    assert fits(flex.contents.to_dict())


@pytest.mark.parametrize("key", OTC_KEYS)
def test_every_otc_message_covers_all_supported_languages(key):
    assert set(_MESSAGES[key]) >= set(LANGUAGES)
    assert all(_MESSAGES[key][lang].strip() for lang in LANGUAGES)


@pytest.mark.parametrize("key", OTC_KEYS)
def test_no_otc_message_contains_markdown(key):
    """LINE 不渲染 Markdown——星號與方括號會原樣出現在長輩的畫面上。"""
    for lang in LANGUAGES:
        text = _MESSAGES[key][lang]
        assert "**" not in text
        assert not re.search(r"\[[^\]]+\]\([^)]+\)", text)
        assert not re.search(r"^\s*#{1,6}\s", text, re.MULTILINE)
        assert not re.search(r"^\s*[-*]\s", text, re.MULTILINE)


@pytest.mark.parametrize("lang", LANGUAGES)
def test_patient_message_never_tells_them_to_stop_or_gives_a_dose(lang):
    """SHALL 引導詢問藥師，SHALL NOT 給劑量建議或指示停藥。"""
    text = _MESSAGES["text.otc.patient.overlap"][lang]

    assert not re.search(r"\d+\s*(mg|毫克|顆|錠|粒|tablets?|pills?)", text, re.I)
    forbidden = ("停藥", "先別吃", "不要吃", "stop taking", "do not take", "服用を中止")
    assert not any(f in text for f in forbidden)
