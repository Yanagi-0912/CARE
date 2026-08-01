from linebot.v3.messaging import FlexMessage

from app.services.line_messaging.flex.medication_flex import (
    build_caregiver_alert_flex,
    build_patient_medication_flex,
    build_patient_urgent_reminder_flex,
    get_slot_display_name,
)


def test_get_slot_display_name():
    assert get_slot_display_name("morning") == "早"
    assert get_slot_display_name("noon") == "中"
    assert get_slot_display_name("evening") == "晚"
    assert get_slot_display_name("bedtime") == "睡前"
    assert get_slot_display_name("custom") == "custom"


def test_build_patient_medication_flex_active():
    msg = build_patient_medication_flex(
        log_id="L123", slot_type="morning", scheduled_time="08:00", disabled=False
    )
    assert isinstance(msg, FlexMessage)
    assert "早" in msg.alt_text
    assert "CARE 用藥提醒" in msg.alt_text


def test_build_patient_medication_flex_disabled():
    msg = build_patient_medication_flex(
        log_id="L123",
        slot_type="evening",
        scheduled_time="18:00",
        disabled=True,
        taken_at_str="18:05",
    )
    assert isinstance(msg, FlexMessage)
    assert "已完成" in msg.alt_text


def test_build_patient_urgent_reminder_flex():
    msg = build_patient_urgent_reminder_flex(
        log_id="L123", slot_type="noon", scheduled_time="12:00"
    )
    assert isinstance(msg, FlexMessage)
    assert "催促" in msg.alt_text


def test_build_caregiver_alert_flex():
    msg = build_caregiver_alert_flex(
        patient_name="王小明", slot_type="bedtime", scheduled_time="21:30"
    )
    assert isinstance(msg, FlexMessage)
    assert "王小明" in msg.alt_text
    assert "逾時未服藥" in msg.alt_text
