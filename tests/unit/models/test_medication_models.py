from datetime import datetime, timezone
from app.models.family_tree import FamilyMember
from app.models.medication import (
    DEFAULT_SLOT_TIMES,
    SLOT_DISPLAY_NAMES,
    MedicationLog,
    MedicationReminder,
)


def test_family_member_care_recipient_field():
    member = FamilyMember(user_id="U123456")
    assert member.user_id == "U123456"
    assert member.is_care_recipient is False

    recipient = FamilyMember(user_id="U123456", is_care_recipient=True)
    assert recipient.is_care_recipient is True


def test_medication_slot_constants():
    assert DEFAULT_SLOT_TIMES["morning"] == "08:00"
    assert DEFAULT_SLOT_TIMES["noon"] == "12:00"
    assert DEFAULT_SLOT_TIMES["evening"] == "18:00"
    assert DEFAULT_SLOT_TIMES["bedtime"] == "21:30"

    assert SLOT_DISPLAY_NAMES["morning"] == "早"
    assert SLOT_DISPLAY_NAMES["noon"] == "中"
    assert SLOT_DISPLAY_NAMES["evening"] == "晚"
    assert SLOT_DISPLAY_NAMES["bedtime"] == "睡前"


def test_medication_reminder_model_creation():
    reminder = MedicationReminder(
        creator_user_id="U_CAREGIVER",
        user_id="U_PATIENT",
        slot_type="morning",
        scheduled_time="08:30",
        start_date="2026-07-25",
        end_date="2026-08-25",
    )
    assert reminder.creator_user_id == "U_CAREGIVER"
    assert reminder.user_id == "U_PATIENT"
    assert reminder.slot_type == "morning"
    assert reminder.scheduled_time == "08:30"
    assert reminder.start_date == "2026-07-25"
    assert reminder.end_date == "2026-08-25"
    assert reminder.enabled is True


def test_medication_log_model_creation():
    now = datetime.now(tz=timezone.utc)
    log = MedicationLog(
        reminder_id="REM_123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_NOTIFY_USER",
        slot_type="morning",
        scheduled_at=now,
        timeout_at=now,
    )
    assert log.reminder_id == "REM_123"
    assert log.user_id == "U_PATIENT"
    assert log.alert_notify_user_id == "U_NOTIFY_USER"
    assert log.status == "pending"
    assert log.taken_at is None
    assert log.patient_reminder_sent is False
    assert log.caregiver_alert_sent is False
