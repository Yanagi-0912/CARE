import inspect
import string
from datetime import datetime

import pytest

from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n.messages import _MESSAGES
from app.services.line_messaging.flex import appointment_flex
from app.services.line_messaging.flex.appointment_flex import (
    build_caregiver_alert_flex,
    build_pre_reminder_flex,
    build_report_ack_flex,
    build_start_reminder_flex,
    format_when,
)
from resources.flex_messages.size_guard import fits

from .support import TPE, rendered

BUILDERS = [
    build_pre_reminder_flex,
    build_start_reminder_flex,
    build_caregiver_alert_flex,
    build_report_ack_flex,
]

APPT_KEYS = sorted(
    key for key in _MESSAGES if key.startswith("flex.appt.") or key.startswith("appt.error.")
)


def _all_cards(language: str):
    common = {"when_text": "9/15（週二）09:30", "hospital_name": "台大醫院", "language": language}
    return [
        build_pre_reminder_flex(reminder_id="A1", **common),
        build_pre_reminder_flex(reminder_id="A1", patient_name="王媽媽", **common),
        build_start_reminder_flex(reminder_id="A1", departed=True, **common),
        build_start_reminder_flex(reminder_id="A1", departed=False, patient_name="王媽媽", **common),
        build_caregiver_alert_flex(reminder_id="A1", patient_name="王媽媽", **common),
        build_caregiver_alert_flex(
            reminder_id="A1", patient_name="王媽媽", departed_time="08:40", **common
        ),
        build_report_ack_flex(
            kind="departed", reported_time="08:40", reporter_name="王小明", **common
        ),
        build_report_ack_flex(
            kind="attended", reported_time="09:35", reporter_name="您", patient_name="王媽媽", **common
        ),
    ]


@pytest.mark.parametrize("builder", BUILDERS)
def test_builders_cannot_even_receive_department_doctor_serial_or_note(builder):
    """隱私邊界守在參數形狀上，不靠呼叫端記得不傳。"""
    params = set(inspect.signature(builder).parameters)
    assert params.isdisjoint({"department", "doctor_name", "serial_number", "note"})


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_every_card_builds_and_fits_in_every_language(language):
    for card in _all_cards(language):
        assert fits(card.contents.to_dict())
        assert len(card.alt_text) <= 400


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_postback_labels_fit_lines_limit(language):
    for card in _all_cards(language):
        body = card.contents.to_dict()
        for label in _postback_labels(body):
            assert len(label) <= 20


def _postback_labels(node):
    if isinstance(node, dict):
        action = node.get("action")
        if isinstance(action, dict) and action.get("type") == "postback":
            yield action["label"]
        for value in node.values():
            yield from _postback_labels(value)
    elif isinstance(node, list):
        for item in node:
            yield from _postback_labels(item)


def test_postback_data_names_the_action_and_the_appointment():
    card = rendered(build_start_reminder_flex(
        reminder_id="A1", when_text="w", hospital_name="h", departed=False
    ))
    assert "action=appointment_attend&appointment_id=A1" in card
    assert "action=appointment_depart&appointment_id=A1" in card


def test_ack_card_has_no_buttons():
    card = build_report_ack_flex(
        kind="attended", when_text="w", hospital_name="h", reported_time="09:35", reporter_name="您"
    )
    assert list(_postback_labels(card.contents.to_dict())) == []


def test_format_when():
    dt = datetime(2026, 9, 15, 9, 30, tzinfo=TPE)
    assert format_when(dt, "zh-TW") == "9/15（週二）09:30"
    assert format_when(dt, "en") == "Tue 9/15, 09:30"
    assert format_when(dt, "vi") == "09:30 T3, 15/9"


@pytest.mark.parametrize("key", APPT_KEYS)
@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_appointment_messages_have_their_own_translation(key, language):
    """收件人包含不讀中文的家屬；t() 缺語言時會靜默落回 zh-TW。"""
    assert _MESSAGES[key].get(language, "").strip()


def _placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


@pytest.mark.parametrize("key", APPT_KEYS)
def test_every_translation_uses_the_same_placeholders(key):
    expected = _placeholders(_MESSAGES[key]["zh-TW"])
    for language in SUPPORTED_LANGUAGES:
        assert _placeholders(_MESSAGES[key][language]) == expected, (key, language)


def test_module_exposes_the_postback_action_names():
    assert appointment_flex.DEPART_ACTION == "appointment_depart"
    assert appointment_flex.ATTEND_ACTION == "appointment_attend"
