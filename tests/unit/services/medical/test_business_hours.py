"""
營業狀態與下次開診時間。

時間一律以參數注入，不使用 monkey patch（專案規則）。
"""

from datetime import datetime

import pytest

from app.schemas import ClinicDaySchedule, ClinicTimeSlot, MedicalFacility
from app.services.medical.business_hours import (
    TAIPEI_TZ,
    BusinessStatus,
    find_next_open,
    has_emergency_department,
    resolve_business_hours,
)

# 2026-08-05 是星期三
WED = datetime(2026, 8, 5, 10, 0, tzinfo=TAIPEI_TZ)
SUN = datetime(2026, 8, 9, 10, 0, tzinfo=TAIPEI_TZ)


def _day(*ranges: tuple[str, str], closed: bool = False) -> ClinicDaySchedule:
    return ClinicDaySchedule(
        isClosed=closed,
        slots=[ClinicTimeSlot(open=o, close=c) for o, c in ranges],
    )


def _facility(
    clinic_time: dict | None = None,
    departments: list[str] | None = None,
    notes: str | None = None,
) -> MedicalFacility:
    return MedicalFacility(
        id="id-1",
        name="測試診所",
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type="西醫診所",
        clinic_time=clinic_time,
        departments=departments,
        notes=notes,
    )


WEEKDAY_SPLIT = {
    "monday": _day(("08:00", "12:00"), ("14:00", "17:30")),
    "tuesday": _day(("08:00", "12:00"), ("14:00", "17:30")),
    "wednesday": _day(("08:00", "12:00"), ("14:00", "17:30")),
    "thursday": _day(("08:00", "12:00"), ("14:00", "17:30")),
    "friday": _day(("08:00", "12:00"), ("14:00", "17:30")),
    "saturday": _day(closed=True),
    "sunday": _day(closed=True),
}


# --- 狀態分級 ---


def test_open_during_slot():
    result = resolve_business_hours(_facility(WEEKDAY_SPLIT), now=WED)
    assert result.status is BusinessStatus.OPEN
    assert result.is_open_now is True


def test_break_between_slots_is_not_closed_today():
    """13:00 落在午休，今天 14:00 還會開 —— 這與「今日已結束」是完全不同的決定。"""
    at_lunch = WED.replace(hour=13, minute=0)
    result = resolve_business_hours(_facility(WEEKDAY_SPLIT), now=at_lunch)

    assert result.status is BusinessStatus.BREAK
    assert result.next_open is not None
    assert result.next_open.is_today is True
    assert result.next_open.time_text == "14:00"


def test_before_first_slot_is_not_break():
    """
    凌晨三點是「今日尚未開診」，不是「午休中」。

    這個 bug 是對真實資料驗證時抓到的：深夜 03:00 有 94.3% 的院所被標成午休中，
    因為原本只判斷「今天還有後續時段」，沒區分「在時段之間」與「在第一個時段之前」。
    """
    before_dawn = WED.replace(hour=3, minute=0)
    result = resolve_business_hours(_facility(WEEKDAY_SPLIT), now=before_dawn)

    assert result.status is BusinessStatus.BEFORE_OPEN
    assert result.next_open.time_text == "08:00"
    assert result.next_open.is_today is True


def test_before_first_slot_in_the_morning():
    """07:30 也是尚未開診（08:00 才開）。"""
    early = WED.replace(hour=7, minute=30)
    assert (
        resolve_business_hours(_facility(WEEKDAY_SPLIT), now=early).status
        is BusinessStatus.BEFORE_OPEN
    )


def test_single_slot_day_before_and_after():
    """只有一個時段的院所：之前是尚未開診，之後是今日已結束，都不是午休。"""
    one_slot = {key: _day(("09:00", "17:00")) for key in WEEKDAY_SPLIT}
    facility = _facility(one_slot)

    before = resolve_business_hours(facility, now=WED.replace(hour=8))
    after = resolve_business_hours(facility, now=WED.replace(hour=18))

    assert before.status is BusinessStatus.BEFORE_OPEN
    assert after.status is BusinessStatus.CLOSED_TODAY


def test_closed_today_after_last_slot():
    after_hours = WED.replace(hour=19, minute=0)
    result = resolve_business_hours(_facility(WEEKDAY_SPLIT), now=after_hours)

    assert result.status is BusinessStatus.CLOSED_TODAY
    assert result.next_open.weekday_key == "thursday"
    assert result.next_open.is_today is False


def test_closed_day_when_is_closed():
    result = resolve_business_hours(_facility(WEEKDAY_SPLIT), now=SUN)
    assert result.status is BusinessStatus.CLOSED_DAY


def test_unknown_when_no_slots_at_all():
    """七天皆無時段是「不知道」，不可誤判為休診。"""
    empty = {key: _day(closed=False) for key in WEEKDAY_SPLIT}
    result = resolve_business_hours(_facility(empty), now=WED)

    assert result.status is BusinessStatus.UNKNOWN
    assert result.next_open is None


def test_unknown_when_clinic_time_missing():
    result = resolve_business_hours(_facility(None), now=WED)
    assert result.status is BusinessStatus.UNKNOWN


# --- 下次開診 ---


def test_next_open_same_day():
    at_lunch = WED.replace(hour=13, minute=0)
    nxt = find_next_open(WEEKDAY_SPLIT, at_lunch)
    assert (nxt.weekday_key, nxt.time_text, nxt.is_today) == ("wednesday", "14:00", True)


def test_next_open_crosses_to_tomorrow():
    after_hours = WED.replace(hour=23, minute=0)
    nxt = find_next_open(WEEKDAY_SPLIT, after_hours)
    assert (nxt.weekday_key, nxt.time_text, nxt.is_today) == ("thursday", "08:00", False)


def test_next_open_crosses_the_weekend():
    """週日只營業平日的院所，下次開診是週一。"""
    nxt = find_next_open(WEEKDAY_SPLIT, SUN)
    assert (nxt.weekday_key, nxt.time_text) == ("monday", "08:00")


def test_next_open_returns_none_when_never_open():
    assert find_next_open({key: _day(closed=True) for key in WEEKDAY_SPLIT}, WED) is None


def test_next_open_skips_slot_already_started():
    """08:30 時，當天 08:00 那個時段已開始，不應被當成「下次」開診。"""
    mid_slot = WED.replace(hour=8, minute=30)
    nxt = find_next_open(WEEKDAY_SPLIT, mid_slot)
    assert nxt.time_text == "14:00"


# --- 急診豁免 ---


def test_emergency_facility_never_shows_closed():
    """
    這是本模組最重要的一條規則。clinicTime 記的是門診時間，
    深夜依門診時間判斷會把 197 家急診醫院全部標成休診。
    """
    at_night = WED.replace(hour=3, minute=0)
    facility = _facility(WEEKDAY_SPLIT, departments=["內科", "急診醫學科"])
    result = resolve_business_hours(facility, now=at_night)

    assert result.status is BusinessStatus.EMERGENCY
    assert result.is_emergency is True
    assert result.is_open_now is True


def test_emergency_wins_over_closed_day_and_notes():
    facility = _facility(
        {key: _day(closed=True) for key in WEEKDAY_SPLIT},
        departments=["急診醫學科"],
        notes="如需看診請先電話洽詢",
    )
    result = resolve_business_hours(facility, now=SUN)
    assert result.status is BusinessStatus.EMERGENCY


def test_emergency_detected_in_dirty_departments():
    """departments 為整串擠在單一元素的髒資料時仍須偵測到急診。"""
    facility = _facility(
        WEEKDAY_SPLIT,
        departments=["家醫科、內科、外科、急診醫學科、牙科"],
    )
    assert has_emergency_department(facility) is True


@pytest.mark.parametrize("departments", [None, [], ["內科"], ["牙科", "眼科"]])
def test_non_emergency_departments(departments):
    assert has_emergency_department(_facility(departments=departments)) is False


# --- notes 兩層規則 ---


def test_date_bound_note_does_not_downgrade_status():
    """
    「春節假期2／17~2／22休診」綁定特定日期。八月因此永久降級會使標籤失去意義，
    所以只顯示原文、不動狀態。
    """
    facility = _facility(WEEKDAY_SPLIT, notes="春節假期2／17~2／22休診")
    result = resolve_business_hours(facility, now=WED)

    assert result.status is BusinessStatus.OPEN
    assert result.note == "春節假期2／17~2／22休診"


def test_evergreen_note_downgrades_to_call_ahead():
    facility = _facility(WEEKDAY_SPLIT, notes="如需看診請先電話洽詢")
    result = resolve_business_hours(facility, now=WED)

    assert result.status is BusinessStatus.CALL_AHEAD
    assert result.note == "如需看診請先電話洽詢"


def test_roc_year_date_counts_as_date_bound():
    """民國年格式「115／01／01」仍含日期樣式，須歸為綁定日期。"""
    facility = _facility(WEEKDAY_SPLIT, notes="115／01／01休診")
    assert resolve_business_hours(facility, now=WED).status is BusinessStatus.OPEN


def test_informational_note_does_not_change_status():
    facility = _facility(WEEKDAY_SPLIT, notes="以提供血液透析服務為主")
    result = resolve_business_hours(facility, now=WED)

    assert result.status is BusinessStatus.OPEN
    assert result.note == "以提供血液透析服務為主"


def test_note_always_returned_regardless_of_status():
    at_night = WED.replace(hour=23, minute=0)
    facility = _facility(WEEKDAY_SPLIT, notes="1／1全日休診")
    result = resolve_business_hours(facility, now=at_night)

    assert result.status is BusinessStatus.CLOSED_TODAY
    assert result.note == "1／1全日休診"
