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
    build_open_now_query,
    find_next_open,
    has_emergency_department,
    is_late_night,
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
    *,
    name: str = "測試診所",
    type_: str = "西醫診所",
) -> MedicalFacility:
    return MedicalFacility(
        id="id-1",
        name=name,
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type=type_,
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
    facility = _facility(
        WEEKDAY_SPLIT, departments=["內科", "急診醫學科"], type_="綜合醫院"
    )
    result = resolve_business_hours(facility, now=at_night)

    assert result.status is BusinessStatus.EMERGENCY
    assert result.is_emergency is True
    assert result.is_open_now is True


def test_emergency_wins_over_closed_day_and_notes():
    facility = _facility(
        {key: _day(closed=True) for key in WEEKDAY_SPLIT},
        departments=["急診醫學科"],
        notes="如需看診請先電話洽詢",
        type_="綜合醫院",
    )
    result = resolve_business_hours(facility, now=SUN)
    assert result.status is BusinessStatus.EMERGENCY


def test_emergency_detected_in_dirty_departments():
    """departments 為整串擠在單一元素的髒資料時仍須偵測到急診。"""
    facility = _facility(
        WEEKDAY_SPLIT,
        departments=["家醫科、內科、外科、急診醫學科、牙科"],
        type_="綜合醫院",
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


# --- 急診只認醫院與衛生所 ---


def test_clinic_listing_emergency_specialty_is_not_emergency():
    """
    departments 列了急診醫學科的 199 家裡有 9 家是一般診所，門診時間都在白天。
    凌晨照「設有急診」列出來，使用者會跑到一間關著的診所。
    """
    at_night = WED.replace(hour=3, minute=0)
    facility = _facility(WEEKDAY_SPLIT, departments=["急診醫學科"])

    assert has_emergency_department(facility) is False
    assert (
        resolve_business_hours(facility, now=at_night).status
        is BusinessStatus.BEFORE_OPEN
    )


def test_island_health_station_with_emergency_department_counts():
    facility = _facility(
        WEEKDAY_SPLIT,
        departments=["內科", "急診醫學科"],
        name="臺東縣蘭嶼鄉衛生所",
        type_="一般診所(醫務室)",
    )
    assert has_emergency_department(facility) is True


# --- 凌晨開始的時段不採信 ---


def test_early_morning_slot_is_not_open_at_night():
    """
    真實資料：週三多一段 00:00–18:00，週一卻是 09:00–12:00。
    照它判斷，這家牙醫診所凌晨兩點半會顯示營業中。
    """
    schedule = {**WEEKDAY_SPLIT, "wednesday": _day(("00:00", "18:00"))}
    result = resolve_business_hours(
        _facility(schedule), now=WED.replace(hour=2, minute=30)
    )

    assert result.status is BusinessStatus.CALL_AHEAD
    assert result.is_open_now is False


def test_early_morning_slot_makes_the_whole_day_call_ahead():
    """不猜哪一段才是真的：同一天白天的時段一樣不採信。"""
    schedule = {
        **WEEKDAY_SPLIT,
        "wednesday": _day(("00:00", "06:00"), ("08:30", "12:00")),
    }
    assert (
        resolve_business_hours(_facility(schedule), now=WED).status
        is BusinessStatus.CALL_AHEAD
    )


def test_early_morning_slot_on_another_day_does_not_leak():
    """只看今天：週三的錯誤資料不影響週一。"""
    schedule = {**WEEKDAY_SPLIT, "wednesday": _day(("00:00", "18:00"))}
    monday = WED.replace(day=3)  # 2026-08-03 是星期一
    assert (
        resolve_business_hours(_facility(schedule), now=monday).status
        is BusinessStatus.OPEN
    )


def test_early_clinic_at_six_thirty_is_trusted():
    """06:30、07:00 開始的是正常早診，不能一起當成資料錯誤。"""
    schedule = {**WEEKDAY_SPLIT, "wednesday": _day(("06:30", "12:00"))}
    assert (
        resolve_business_hours(_facility(schedule), now=WED.replace(hour=7)).status
        is BusinessStatus.OPEN
    )


def test_next_open_skips_early_morning_slot():
    """「下次開診 週三 00:00」是把錯誤資料講給使用者聽。"""
    schedule = {**WEEKDAY_SPLIT, "wednesday": _day(("00:00", "18:00"))}
    tuesday_night = WED.replace(day=4, hour=22)  # 2026-08-04 是星期二
    nxt = find_next_open(schedule, tuesday_night)
    assert (nxt.weekday_key, nxt.time_text) == ("thursday", "08:00")


def test_slot_running_into_early_morning_is_call_ahead():
    """
    同一筆跨夜錯誤資料的前半段：週二開到 24:00、週三又從 00:00 接下去，
    其他平日卻是 14:00–17:30。套用凌晨規則後，週二 23:30 仍判定營業中的
    26 家裡有 23 家是這樣。
    """
    schedule = {
        **WEEKDAY_SPLIT,
        "tuesday": _day(("14:00", "24:00")),
        "wednesday": _day(("00:00", "05:30"), ("09:00", "12:00")),
    }
    tuesday_night = WED.replace(day=4, hour=23, minute=30)  # 2026-08-04 是星期二
    assert (
        resolve_business_hours(_facility(schedule), now=tuesday_night).status
        is BusinessStatus.CALL_AHEAD
    )


def test_late_clinic_without_overnight_chain_is_trusted():
    """開到半夜、隔天正常時間才開的（例如每天 09:00–24:00 的醫院）照常採信。"""
    late = {key: _day(("09:00", "24:00")) for key in WEEKDAY_SPLIT}
    tuesday_night = WED.replace(day=4, hour=23, minute=30)
    assert (
        resolve_business_hours(_facility(late), now=tuesday_night).status
        is BusinessStatus.OPEN
    )


# --- Mongo 查詢條件 ---


def test_open_now_query_targets_todays_slots():
    query = build_open_now_query(WED.replace(hour=23, minute=30))
    in_clinic_hours = query["$or"][1]

    assert in_clinic_hours["clinicTime.wednesday.slots"]["$elemMatch"] == {
        "open": {"$gte": "06:00", "$lte": "23:30"},
        "close": {"$gte": "23:30"},
    }
    assert in_clinic_hours["clinicTime.wednesday.isClosed"] == {"$ne": True}


def test_open_now_query_before_dawn_leaves_only_emergency():
    """凌晨時門診那一支要開始時間 ≥ 06:00 又 ≤ 02:30，必然落空，只剩急診。"""
    query = build_open_now_query(WED.replace(hour=2, minute=30))
    open_range = query["$or"][1]["clinicTime.wednesday.slots"]["$elemMatch"]["open"]

    assert open_range["$gte"] > open_range["$lte"]
    assert "急診" in query["$or"][0]["departments"]["$regex"]


# --- 深夜 ---


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (22, 59, False),
        (23, 0, True),
        (2, 30, True),
        (5, 59, True),
        (6, 0, False),
        (12, 0, False),
    ],
)
def test_late_night_window(hour, minute, expected):
    assert is_late_night(WED.replace(hour=hour, minute=minute)) is expected
