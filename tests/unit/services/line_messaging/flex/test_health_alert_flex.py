"""超出範圍推播卡的版面與內容邊界（health-alerts spec「超出範圍推播的
內容」）：數值與被超過的範圍值同時呈現、代記者註明、不含診斷/治療建議/
119 區塊、在最大字級下仍不超過 LINE 的大小上限。
"""

from datetime import datetime, timezone

import pytest

from app.i18n.messages import _MESSAGES
from app.models.health import HealthMeasurement
from app.services.line_messaging.flex.health_alert_flex import (
    build_health_alert_flex,
    health_alert_alt_text,
)
from resources.flex_messages.size_guard import fits

LANGUAGES = ("zh-TW", "en", "id", "vi", "th", "ja")
FONT_SIZES = ("normal", "large", "xlarge")
HEALTH_ALERT_KEYS = [k for k in _MESSAGES if k.startswith("flex.health_alert.")]


def _bp_measurement(**overrides) -> HealthMeasurement:
    base = dict(
        id="M1",
        user_id="U_ELDER",
        kind="blood_pressure",
        measured_at=datetime(2026, 1, 1, 8, 30, tzinfo=timezone.utc),
        recorded_by="U_ELDER",
        systolic=152,
        diastolic=90,
        level="above_range",
    )
    base.update(overrides)
    return HealthMeasurement(**base)


def _glucose_measurement(**overrides) -> HealthMeasurement:
    base = dict(
        id="M2",
        user_id="U_ELDER",
        kind="blood_glucose",
        measured_at=datetime(2026, 1, 1, 8, 30, tzinfo=timezone.utc),
        recorded_by="U_ELDER",
        glucose_mg_dl=65,
        meal_context="fasting",
        level="below_range",
    )
    base.update(overrides)
    return HealthMeasurement(**base)


def test_content_carries_both_the_value_and_the_exceeded_bound():
    flex = build_health_alert_flex(
        alert_key="bp_high",
        measurement=_bp_measurement(),
        exceeded=[("systolic", "upper", 140)],
        language="zh-TW",
    )
    rendered = str(flex.contents.to_dict())

    assert "152" in rendered
    assert "140" in rendered


def test_recorder_note_appears_only_when_proxy_recorded():
    with_recorder = build_health_alert_flex(
        alert_key="bp_high",
        measurement=_bp_measurement(recorded_by="U_GUARDIAN"),
        recorder_name="小美",
        language="zh-TW",
    )
    assert "小美" in str(with_recorder.contents.to_dict())

    without_recorder = build_health_alert_flex(
        alert_key="bp_high", measurement=_bp_measurement(), language="zh-TW"
    )
    assert "記錄" not in str(without_recorder.contents.to_dict())


def test_patient_name_shown_for_family_recipients_only():
    for_family = build_health_alert_flex(
        alert_key="bp_high",
        measurement=_bp_measurement(),
        patient_name="王長輩",
        language="zh-TW",
    )
    assert "王長輩" in str(for_family.contents.to_dict())

    for_owner = build_health_alert_flex(
        alert_key="bp_high", measurement=_bp_measurement(), language="zh-TW"
    )
    assert "王長輩" not in str(for_owner.contents.to_dict())


def test_glucose_card_shows_meal_context():
    flex = build_health_alert_flex(
        alert_key="glucose_low",
        measurement=_glucose_measurement(),
        exceeded=[("glucose", "lower", 70)],
        language="zh-TW",
    )
    rendered = str(flex.contents.to_dict())

    assert "65" in rendered
    assert "70" in rendered
    assert "空腹" in rendered  # fasting


def test_card_contains_no_diagnosis_advice_or_emergency_block():
    """SHALL NOT 含診斷、治療建議或緊急撥號區塊（design.md 決策 10）。"""
    flex = build_health_alert_flex(
        alert_key="bp_high", measurement=_bp_measurement(), language="zh-TW"
    )
    rendered = str(flex.contents.to_dict())

    for forbidden in ("119", "diagnos", "診斷", "建議您", "治療"):
        assert forbidden not in rendered


def test_button_omitted_when_no_uri_given():
    flex = build_health_alert_flex(
        alert_key="bp_high", measurement=_bp_measurement(), language="zh-TW", button_uri=""
    )
    assert "footer" not in flex.contents.to_dict()


def test_button_uri_present_when_given():
    flex = build_health_alert_flex(
        alert_key="bp_high",
        measurement=_bp_measurement(),
        language="zh-TW",
        button_uri="https://liff.line.me/1234/health-records",
    )
    assert "https://liff.line.me/1234/health-records" in str(flex.contents.to_dict())


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("font_size", FONT_SIZES)
def test_card_fits_size_guard_at_every_font_size(language, font_size):
    flex = build_health_alert_flex(
        alert_key="bp_high",
        measurement=_bp_measurement(pulse=72),
        exceeded=[("systolic", "upper", 140), ("diastolic", "upper", 85)],
        recorder_name="家人姓名比較長的那種情形",
        patient_name="另一個比較長的姓名情形",
        button_uri="https://liff.line.me/1234/health-records?user=U_ELDER",
        language=language,
        font_size=font_size,
    )
    assert fits(flex.contents.to_dict())


@pytest.mark.parametrize("key", HEALTH_ALERT_KEYS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_every_health_alert_key_has_every_language(key, language):
    assert _MESSAGES[key].get(language)


@pytest.mark.parametrize("alert_key", ["bp_high", "bp_low", "glucose_high", "glucose_low"])
def test_alt_text_has_no_untranslated_key_leak(alert_key):
    for language in LANGUAGES:
        assert not health_alert_alt_text(alert_key, language).startswith("flex.health_alert.")
