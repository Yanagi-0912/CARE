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
    assert "尚未確認" in msg.alt_text


def test_build_caregiver_alert_flex():
    msg = build_caregiver_alert_flex(
        patient_name="王小明", slot_type="bedtime", scheduled_time="21:30"
    )
    assert isinstance(msg, FlexMessage)
    assert "王小明" in msg.alt_text
    assert "逾時未服藥" in msg.alt_text


def test_medication_flex_follows_language_setting():
    msg = build_patient_medication_flex(
        log_id="L123", slot_type="morning", scheduled_time="08:00", language="en"
    )
    body = str(msg.contents.to_dict())
    assert "Morning" in body
    assert "I took it" in body
    assert "早" not in body


def test_medication_flex_follows_font_size_setting():
    normal = build_patient_medication_flex(
        log_id="L1", slot_type="morning", scheduled_time="08:00", font_size="normal"
    )
    xlarge = build_patient_medication_flex(
        log_id="L1", slot_type="morning", scheduled_time="08:00", font_size="xlarge"
    )
    # body 第一塊是時段重點區塊，其首行為時段名稱
    normal_slot = normal.contents.to_dict()["body"]["contents"][0]["contents"][0]
    xlarge_slot = xlarge.contents.to_dict()["body"]["contents"][0]["contents"][0]
    assert normal_slot["size"] == "xl"
    assert xlarge_slot["size"] == "4xl"


def test_caregiver_alert_follows_language_setting():
    msg = build_caregiver_alert_flex(
        patient_name="王小明", slot_type="bedtime", scheduled_time="21:30", language="ja"
    )
    assert "王小明" in msg.alt_text
    assert "就寝前" in str(msg.contents.to_dict())


def test_unknown_slot_type_falls_back_to_raw_value():
    # 未知時段不應輸出 i18n key 字串
    msg = build_patient_medication_flex(
        log_id="L1", slot_type="brunch", scheduled_time="10:00"
    )
    body = str(msg.contents.to_dict())
    assert "brunch" in body
    assert "slot.brunch" not in body
