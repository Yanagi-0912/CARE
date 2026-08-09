from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.family_tree import FamilyMember
from app.models.medication import (
    DEFAULT_SLOT_TIMES,
    SLOT_DISPLAY_NAMES,
    TAIPEI_TZ,
    CreateMedicationReminderRequest,
    MedicationLog,
    MedicationReminder,
    UpdateMedicationReminderRequest,
    ensure_aware_utc,
    to_taipei_hm,
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


# ── 時區轉換 ────────────────────────────────────────────────────────


def test_ensure_aware_utc_treats_naive_as_utc():
    naive = datetime(2026, 7, 29, 0, 0)
    assert ensure_aware_utc(naive).tzinfo == timezone.utc


def test_ensure_aware_utc_keeps_existing_tzinfo():
    aware = datetime(2026, 7, 29, 8, 0, tzinfo=TAIPEI_TZ)
    assert ensure_aware_utc(aware) is aware


def test_to_taipei_hm_converts_naive_utc_from_database():
    """
    Motor client 未啟用 tz_aware，pymongo 讀回來的是 naive UTC。
    台北 08:00 的提醒在資料庫是 00:00Z，直接 strftime 會顯示 00:00。
    """
    from_db = datetime(2026, 7, 29, 0, 0)  # 台北 08:00 存進 Mongo 後讀回的樣子
    assert to_taipei_hm(from_db) == "08:00"


def test_to_taipei_hm_handles_aware_inputs():
    assert to_taipei_hm(datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)) == "08:00"
    assert to_taipei_hm(datetime(2026, 7, 29, 8, 0, tzinfo=TAIPEI_TZ)) == "08:00"


def test_to_taipei_hm_crosses_date_boundary():
    # UTC 2026-07-28 17:30 = 台北 2026-07-29 01:30
    assert to_taipei_hm(datetime(2026, 7, 28, 17, 30)) == "01:30"


def test_to_taipei_hm_returns_default_for_none():
    assert to_taipei_hm(None) == ""
    assert to_taipei_hm(None, default="08:00") == "08:00"


# ── slot_times 驗證 ─────────────────────────────────────────────────


def test_create_request_accepts_valid_slot_times():
    req = CreateMedicationReminderRequest(
        user_id="U_SELF",
        slots=["morning", "bedtime"],
        slot_times={"morning": "07:30", "bedtime": "23:59"},
    )
    assert req.slot_times == {"morning": "07:30", "bedtime": "23:59"}


@pytest.mark.parametrize("bad_time", ["9am", "7:30", "24:00", "08:60", "0730", "", "08:00:00"])
def test_create_request_rejects_malformed_slot_time(bad_time):
    """
    格式錯誤若寫進資料庫，排程器的 strptime 會拋錯並被 except 吞掉 ——
    該筆提醒永遠不會觸發，也不會有任何錯誤回饋。必須在入口擋掉。
    """
    with pytest.raises(ValidationError):
        CreateMedicationReminderRequest(
            user_id="U_SELF",
            slots=["morning"],
            slot_times={"morning": bad_time},
        )


def test_create_request_rejects_unknown_slot_key():
    with pytest.raises(ValidationError):
        CreateMedicationReminderRequest(
            user_id="U_SELF",
            slots=["morning"],
            slot_times={"moring": "07:30"},  # typo，不擋會被無聲忽略
        )


def test_update_request_rejects_malformed_scheduled_time():
    with pytest.raises(ValidationError):
        UpdateMedicationReminderRequest(scheduled_time="8點")

    assert UpdateMedicationReminderRequest(scheduled_time="08:05").scheduled_time == "08:05"
    assert UpdateMedicationReminderRequest().scheduled_time is None


def test_reminder_medication_ids_defaults_to_empty_list():
    reminder = MedicationReminder(
        creator_user_id="U_FAMILY",
        user_id="U_PATIENT",
        slot_type="morning",
    )

    assert reminder.medication_ids == []


def test_reminder_reads_back_without_medication_ids_field():
    """本變更前寫入的規則沒有 medication_ids 欄位，讀回時必須仍然成立。"""
    document = {
        "_id": "R1",
        "creator_user_id": "U_FAMILY",
        "user_id": "U_PATIENT",
        "slot_type": "evening",
        "scheduled_time": "18:00",
        "start_date": "2026-08-09",
        "enabled": True,
    }

    reminder = MedicationReminder(**document)

    assert reminder.medication_ids == []
    assert reminder.slot_type == "evening"


def test_reminder_medication_ids_are_independent_between_instances():
    """default_factory 的驗證：兩個實例不得共用同一個 list。"""
    first = MedicationReminder(
        creator_user_id="U_FAMILY", user_id="U_PATIENT", slot_type="morning"
    )
    second = MedicationReminder(
        creator_user_id="U_FAMILY", user_id="U_PATIENT", slot_type="noon"
    )

    first.medication_ids.append("M1")

    assert second.medication_ids == []


def test_medication_defaults():
    from app.models.medication import Medication

    med = Medication(
        user_id="U_PATIENT",
        created_by_user_id="U_FAMILY",
        name="脈優錠5毫克",
    )

    assert med.enabled is True
    assert med.source == "manual"
    assert med.end_date is None
    assert med.frequency_code == "OTHER"
    assert med.start_date == datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


def test_medication_rejects_unknown_source():
    from app.models.medication import Medication

    with pytest.raises(ValidationError):
        Medication(
            user_id="U_PATIENT",
            created_by_user_id="U_FAMILY",
            name="某藥",
            source="imported_from_somewhere",
        )


def test_medication_keeps_usage_raw_and_indication():
    from app.models.medication import Medication

    med = Medication(
        user_id="U_PATIENT",
        created_by_user_id="U_FAMILY",
        name="某藥",
        usage_raw="TID PC",
        indication="高血壓",
        license_number="衛署藥製字第000001號",
        source="prescription_ocr",
    )

    assert med.usage_raw == "TID PC"
    assert med.indication == "高血壓"
    assert med.license_number == "衛署藥製字第000001號"
    assert med.source == "prescription_ocr"
