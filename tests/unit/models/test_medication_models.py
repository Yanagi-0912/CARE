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
    ReminderEntry,
    UpdateMedicationReminderRequest,
    derive_entry_fields,
    ensure_aware_utc,
    to_taipei_hm,
    validate_entries,
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


# ── entries（飯前飯後條目）──────────────────────────────────────────


def test_reminder_reads_back_without_entries_synthesizes_none_entry():
    """本變更前寫入的規則沒有 entries 欄位，讀回時須合成單一 none 條目，
    且 timeout_anchor_time 等於 scheduled_time（spec「既有規則沒有條目欄位」）。
    """
    document = {
        "_id": "R1",
        "creator_user_id": "U_FAMILY",
        "user_id": "U_PATIENT",
        "slot_type": "evening",
        "scheduled_time": "18:00",
        "medication_ids": ["M1", "M2"],
        "start_date": "2026-08-09",
        "enabled": True,
    }

    reminder = MedicationReminder(**document)

    assert len(reminder.entries) == 1
    entry = reminder.entries[0]
    assert entry.meal_timing == "none"
    assert entry.scheduled_time == "18:00"
    assert entry.medication_ids == ["M1", "M2"]
    assert reminder.scheduled_time == "18:00"
    assert reminder.timeout_anchor_time == "18:00"
    assert reminder.medication_ids == ["M1", "M2"]


def test_reminder_with_two_entries_derives_min_max_and_union():
    """飯前飯後兩個條目：scheduled_time 取最早、timeout_anchor_time 取最晚、
    medication_ids 為依條目順序去重的聯集（spec「早上飯前飯後各一批藥」）。
    """
    reminder = MedicationReminder(
        creator_user_id="U_FAMILY",
        user_id="U_PATIENT",
        slot_type="morning",
        entries=[
            {
                "meal_timing": "after_meal",
                "scheduled_time": "08:30",
                "medication_ids": ["M2", "M3"],
            },
            {
                "meal_timing": "before_meal",
                "scheduled_time": "07:30",
                "medication_ids": ["M1"],
            },
        ],
    )

    assert reminder.scheduled_time == "07:30"
    assert reminder.timeout_anchor_time == "08:30"
    assert reminder.medication_ids == ["M1", "M2", "M3"]
    # entries 依 MEAL_TIMING_ORDER（before_meal, after_meal, none）排序，
    # 不是輸入時的原始順序。
    assert [entry.meal_timing for entry in reminder.entries] == [
        "before_meal",
        "after_meal",
    ]


def test_reminder_rejects_duplicate_meal_timing():
    with pytest.raises(ValidationError):
        MedicationReminder(
            creator_user_id="U_FAMILY",
            user_id="U_PATIENT",
            slot_type="morning",
            entries=[
                {"meal_timing": "before_meal", "scheduled_time": "07:30", "medication_ids": []},
                {"meal_timing": "before_meal", "scheduled_time": "08:00", "medication_ids": []},
            ],
        )


def test_reminder_rejects_same_medication_across_entries():
    with pytest.raises(ValidationError):
        MedicationReminder(
            creator_user_id="U_FAMILY",
            user_id="U_PATIENT",
            slot_type="morning",
            entries=[
                {"meal_timing": "before_meal", "scheduled_time": "07:30", "medication_ids": ["M1"]},
                {"meal_timing": "after_meal", "scheduled_time": "08:30", "medication_ids": ["M1"]},
            ],
        )


def test_validate_entries_rejects_empty_list():
    with pytest.raises(ValueError):
        validate_entries([])


def test_derive_entry_fields_returns_plain_dicts_sorted_by_meal_timing_order():
    entries = [
        ReminderEntry(meal_timing="none", scheduled_time="12:00", medication_ids=["M9"]),
        ReminderEntry(meal_timing="before_meal", scheduled_time="11:30", medication_ids=["M1"]),
    ]

    derived = derive_entry_fields(entries)

    assert derived["scheduled_time"] == "11:30"
    assert derived["timeout_anchor_time"] == "12:00"
    assert derived["medication_ids"] == ["M1", "M9"]
    assert [e["meal_timing"] for e in derived["entries"]] == ["before_meal", "none"]
    assert all(isinstance(e, dict) for e in derived["entries"])


def test_slot_entries_rejects_malformed_time():
    """slot_entries 裡條目的時間格式錯誤須擋在請求驗證層，理由同 slot_times：
    格式錯誤若寫進資料庫，排程器的 strptime 會拋錯並被 except 吞掉。"""
    with pytest.raises(ValidationError):
        CreateMedicationReminderRequest(
            user_id="U_SELF",
            slots=["morning"],
            slot_entries={
                "morning": [
                    {"meal_timing": "none", "scheduled_time": "9am", "medication_ids": []}
                ]
            },
        )


def test_slot_entries_rejects_duplicate_meal_timing_within_slot():
    with pytest.raises(ValidationError):
        CreateMedicationReminderRequest(
            user_id="U_SELF",
            slots=["morning"],
            slot_entries={
                "morning": [
                    {"meal_timing": "before_meal", "scheduled_time": "07:00", "medication_ids": []},
                    {"meal_timing": "before_meal", "scheduled_time": "07:30", "medication_ids": []},
                ]
            },
        )


def test_update_request_entries_validated_same_as_reminder():
    with pytest.raises(ValidationError):
        UpdateMedicationReminderRequest(
            entries=[
                {"meal_timing": "none", "scheduled_time": "07:00", "medication_ids": ["M1"]},
                {"meal_timing": "before_meal", "scheduled_time": "07:30", "medication_ids": ["M1"]},
            ]
        )

    ok = UpdateMedicationReminderRequest(
        entries=[
            {"meal_timing": "before_meal", "scheduled_time": "07:30", "medication_ids": ["M1"]},
        ]
    )
    assert ok.entries[0].meal_timing == "before_meal"


# ── MedicationLog：urgent_at / taken_medication_ids ────────────────


def test_log_reads_back_without_urgent_at_and_taken_medication_ids():
    """本變更前寫入的紀錄沒有這兩個欄位，讀回時須維持過去行為：
    urgent_at 為 None、taken_medication_ids 為空陣列。"""
    now = datetime.now(tz=timezone.utc)
    document = {
        "_id": "LOG1",
        "reminder_id": "REM_123",
        "user_id": "U_PATIENT",
        "alert_notify_user_id": "U_NOTIFY_USER",
        "slot_type": "morning",
        "scheduled_at": now,
        "timeout_at": now,
    }

    log = MedicationLog(**document)

    assert log.urgent_at is None
    assert log.taken_medication_ids == []


# ── CreateMedicationRequest ─────────────────────────────────────────


def test_create_medication_request_strips_name_whitespace():
    from app.models.medication import CreateMedicationRequest

    req = CreateMedicationRequest(user_id="U_SELF", name="  脈優錠5毫克  ")
    assert req.name == "脈優錠5毫克"


def test_create_medication_request_rejects_blank_name_after_strip():
    from app.models.medication import CreateMedicationRequest

    with pytest.raises(ValidationError):
        CreateMedicationRequest(user_id="U_SELF", name="   ")


# ── start_date／end_date 的格式驗證 ─────────────────────────────────────
#
# 兩個日期在資料庫裡是字串，排程器的日期區間查詢是拿字串直接比大小，只有零填補
# 的 YYYY-MM-DD 才成立。"2026/9/1" 寫進去不會報錯，只會讓那筆規則永遠比不進
# 今天的區間、永遠不推播，而且沒有任何回饋——與 scheduled_time 同一種靜默失效。


@pytest.mark.parametrize("bad", ["2026/09/01", "9-1-2026", "20260901", "2026-9-1", "2026-13-45", "今天"])
@pytest.mark.parametrize("field", ["start_date", "end_date"])
def test_create_request_rejects_dates_that_are_not_iso(field, bad):
    with pytest.raises(ValidationError) as exc_info:
        CreateMedicationReminderRequest(user_id="U", slots=["morning"], **{field: bad})
    assert "YYYY-MM-DD" in str(exc_info.value) or "有效的日期" in str(exc_info.value)
    assert field in str(exc_info.value)


def test_create_request_accepts_iso_dates_and_none():
    request = CreateMedicationReminderRequest(
        user_id="U", slots=["morning"], start_date="2026-09-01", end_date=None
    )
    assert request.start_date == "2026-09-01"
    assert request.end_date is None


@pytest.mark.parametrize("field", ["start_date", "end_date"])
def test_update_request_rejects_dates_that_are_not_iso(field):
    with pytest.raises(ValidationError) as exc_info:
        UpdateMedicationReminderRequest(**{field: "2026/09/01"})
    assert field in str(exc_info.value)


def test_update_request_still_allows_explicit_null_end_date():
    """null 是把療程改回長期的唯一途徑，格式驗證不能把它擋掉；null 的合法性由
    服務層依 NULLABLE_FIELDS 判定。"""
    request = UpdateMedicationReminderRequest(end_date=None)
    assert request.end_date is None
    assert "end_date" in request.model_fields_set
