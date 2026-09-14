import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models.appointment import (
    AppointmentReminder,
    AppointmentReminderResponse,
    UpdateAppointmentReminderRequest,
    actionable_from,
    day_end_for,
)

from .support import TPE, make_appointment

JST = timezone(timedelta(hours=9))


# ── notify_at ────────────────────────────────────────────────────────


def test_notify_at_is_t_minus_1h_t0_t_plus_30_in_the_appointments_own_offset():
    reminder = make_appointment()
    assert [dt.isoformat() for dt in reminder.notify_at()] == [
        "2026-09-15T08:30:00+08:00",
        "2026-09-15T09:30:00+08:00",
        "2026-09-15T10:00:00+08:00",
    ]


def test_notify_at_keeps_all_three_after_departing():
    """出發之後 T+0 與 T+30 仍會推（T+30 看的是「不是 attended」）。"""
    assert len(make_appointment(status="departed").notify_at()) == 3


@pytest.mark.parametrize("status", ["attended", "missed", "cancelled"])
def test_notify_at_is_empty_once_no_more_pushes_will_happen(status):
    assert make_appointment(status=status).notify_at() == []


def test_notify_at_is_empty_when_reminders_are_turned_off():
    assert make_appointment(enabled=False).notify_at() == []


# ── 時區：原樣送、原樣顯示 ────────────────────────────────────────────


def test_mongo_round_trip_keeps_the_offset_the_user_sent():
    """Motor 以 naive UTC 讀回；輸出必須還原成使用者送來的 +08:00，
    而不是 UTC、也不是沒有 offset 的字串。"""
    stored = make_appointment(id="A1").model_dump(by_alias=True)
    stored["appointment_at"] = datetime(2026, 9, 15, 1, 30)  # naive UTC
    stored["created_at"] = datetime(2026, 9, 12, 1, 30)
    reminder = AppointmentReminder(**stored)

    body = json.loads(reminder.to_response().model_dump_json())
    assert body["appointment_at"] == "2026-09-15T09:30:00+08:00"
    assert body["created_at"] == "2026-09-12T09:30:00+08:00"


def test_a_non_taipei_offset_is_echoed_back_unchanged():
    reminder = make_appointment(at=datetime(2026, 9, 15, 9, 30, tzinfo=JST), id="A1")
    body = json.loads(reminder.to_response().model_dump_json())
    assert body["appointment_at"] == "2026-09-15T09:30:00+09:00"
    assert body["notify_at"][0] == "2026-09-15T08:30:00+09:00"


# ── 回應形狀 ──────────────────────────────────────────────────────────


def test_response_always_has_every_key_with_null_for_missing_values():
    body = json.loads(make_appointment(id="A1").to_response().model_dump_json())
    assert set(body) == set(AppointmentReminderResponse.model_fields)
    for nullable in (
        "hospital_address",
        "hospital_phone",
        "doctor_name",
        "serial_number",
        "note",
        "departed_at",
        "departed_by_user_id",
        "attended_at",
        "attended_by_user_id",
    ):
        assert nullable in body
        assert body[nullable] is None


def test_response_does_not_expose_scheduler_bookkeeping():
    body = json.loads(make_appointment(id="A1").to_response().model_dump_json())
    for internal in (
        "pre_reminder_sent",
        "start_reminder_sent",
        "caregiver_alert_sent",
        "pre_reminder_attempts",
        "day_end_at",
        "appointment_utc_offset_minutes",
    ):
        assert internal not in body


# ── 當日結束與可回報時間 ──────────────────────────────────────────────


def test_day_end_is_local_midnight():
    at = datetime(2026, 9, 15, 9, 30, tzinfo=TPE)
    assert day_end_for(at, 480) == datetime(2026, 9, 15, 16, 0, tzinfo=timezone.utc)


def test_day_end_is_never_before_the_caregiver_alert_window():
    """23:45 的診照字面在 00:00 標記 missed，T+30（00:15）就永遠發不出去。"""
    at = datetime(2026, 9, 15, 23, 45, tzinfo=TPE)
    assert day_end_for(at, 480) == at + timedelta(minutes=60)


def test_day_end_follows_the_users_offset_not_the_servers():
    at = datetime(2026, 9, 15, 9, 30, tzinfo=JST)
    assert day_end_for(at, 540) == datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)


def test_actionable_from_is_the_local_start_of_the_appointment_day():
    at = datetime(2026, 9, 15, 9, 30, tzinfo=TPE)
    assert actionable_from(at, 480) == datetime(2026, 9, 15, 0, 0, tzinfo=TPE)


def test_actionable_from_lets_the_pre_reminder_button_work_for_early_morning_visits():
    """00:30 的診，T-1h 卡片在前一天 23:30 送達，卡片上的按鈕必須按得下去。"""
    at = datetime(2026, 9, 15, 0, 30, tzinfo=TPE)
    assert actionable_from(at, 480) == datetime(2026, 9, 14, 23, 30, tzinfo=TPE)


# ── PUT：exclude_unset 的前提 ─────────────────────────────────────────


def test_put_request_distinguishes_an_absent_key_from_an_explicit_null():
    assert UpdateAppointmentReminderRequest.model_validate({}).model_dump(
        exclude_unset=True
    ) == {}
    assert UpdateAppointmentReminderRequest.model_validate(
        {"doctor_name": None}
    ).model_dump(exclude_unset=True) == {"doctor_name": None}
