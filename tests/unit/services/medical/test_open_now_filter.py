"""
`open_now` 過濾行為。

最重要的一條是「急診不得被排除」：clinicTime 記的是門診時間，
依它篩選會在深夜把所有急診醫院藏起來。

時間一律用 clock 注入，不依賴跑測試當下幾點。
"""

from datetime import datetime

import pytest

from app.schemas import ClinicDaySchedule, ClinicTimeSlot, MedicalFacility
from app.services.medical.business_hours import TAIPEI_TZ, build_open_now_query
from app.services.medical.department_matcher import build_department_query
from app.services.medical.medical_service import (
    OPEN_NOW_OVERFETCH_LIMIT,
    MedicalService,
)

_WEEKDAYS = (
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
)

# 2026-09-16 是星期三
WEDNESDAY_MORNING = datetime(2026, 9, 16, 10, 0, tzinfo=TAIPEI_TZ)
WEDNESDAY_NIGHT = datetime(2026, 9, 16, 2, 30, tzinfo=TAIPEI_TZ)


def _open_daytime() -> dict[str, ClinicDaySchedule]:
    return {
        key: ClinicDaySchedule(
            isClosed=False, slots=[ClinicTimeSlot(open="08:00", close="21:00")]
        )
        for key in _WEEKDAYS
    }


def _always_closed() -> dict[str, ClinicDaySchedule]:
    return {key: ClinicDaySchedule(isClosed=True, slots=[]) for key in _WEEKDAYS}


def _facility(
    name: str,
    distance_meters: float,
    *,
    open_daytime: bool = True,
    departments: list[str] | None = None,
    type_: str = "西醫診所",
) -> MedicalFacility:
    return MedicalFacility(
        id=f"id-{name}",
        name=name,
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type=type_,
        clinic_time=_open_daytime() if open_daytime else _always_closed(),
        departments=departments,
        distance_meters=distance_meters,
    )


class FakeRepository:
    """不理會 query、只依距離回傳；營業篩選的正確性由應用層的最後判斷把關。"""

    def __init__(self, facilities: list[MedicalFacility]) -> None:
        self._facilities = facilities
        self.calls: list[dict] = []

    async def find_near(self, lat, lng, radius_meters, limit, query=None):
        self.calls.append({"limit": limit, "query": query})
        return [
            f for f in self._facilities if (f.distance_meters or 0) <= radius_meters
        ][:limit]


def _service(
    repository: FakeRepository, now: datetime = WEDNESDAY_MORNING
) -> MedicalService:
    return MedicalService(repository=repository, clock=lambda: now)


@pytest.mark.asyncio
async def test_open_now_filters_out_closed_facilities():
    facilities = [
        _facility("關的1", 100, open_daytime=False),
        _facility("開的1", 200),
        _facility("關的2", 300, open_daytime=False),
        _facility("開的2", 400),
    ]
    service = _service(FakeRepository(facilities))

    result = await service.find_nearby_hospitals(25.0, 121.0, open_now=True)

    assert [f.name for f in result.facilities] == ["開的1", "開的2"]
    assert result.open_now_requested is True
    assert result.open_now_fallback is False


@pytest.mark.asyncio
async def test_emergency_facility_survives_open_now_filter():
    """
    急診醫院的 clinicTime 是門診時間，深夜會判定為休診。
    但把急診藏起來會讓急需就醫的使用者被告知「附近沒有院所」。
    """
    facilities = [
        _facility("一般診所", 100, open_daytime=False),
        _facility(
            "急診醫院",
            200,
            open_daytime=False,
            departments=["內科", "急診醫學科"],
            type_="綜合醫院",
        ),
    ]
    service = _service(FakeRepository(facilities), now=WEDNESDAY_NIGHT)

    result = await service.find_nearby_hospitals(25.0, 121.0, open_now=True)

    assert [f.name for f in result.facilities] == ["急診醫院"]
    assert result.open_now_fallback is False


@pytest.mark.asyncio
async def test_open_now_condition_is_pushed_into_the_query():
    """
    營業條件要交給 Mongo 篩，不能只在最近 20 家裡篩：城市裡最近 20 家都在
    600 公尺內、深夜全關，實測 9 個地點在凌晨全部退回列一排沒開的。
    """
    repository = FakeRepository([_facility("開的", 100)])

    await _service(repository, now=WEDNESDAY_NIGHT).find_nearby_hospitals(
        25.0, 121.0, open_now=True
    )

    assert repository.calls[0]["query"] == build_open_now_query(WEDNESDAY_NIGHT)


@pytest.mark.asyncio
async def test_open_now_query_keeps_department_filter():
    repository = FakeRepository([_facility("開的內科", 100, departments=["內科"])])

    await _service(repository).find_nearby_facilities_by_department(
        25.0, 121.0, "內科", open_now=True
    )

    assert repository.calls[0]["query"] == {
        "$and": [
            build_department_query("內科"),
            build_open_now_query(WEDNESDAY_MORNING),
        ]
    }


@pytest.mark.asyncio
async def test_falls_back_when_nothing_is_open():
    """50 公里內一家都沒開時，改列最近的院所，而非回「查無院所」。"""
    facilities = [
        _facility("關的1", 100, open_daytime=False),
        _facility("關的2", 200, open_daytime=False),
    ]
    repository = FakeRepository(facilities)

    result = await _service(repository).find_nearby_hospitals(
        25.0, 121.0, open_now=True
    )

    assert [f.name for f in result.facilities] == ["關的1", "關的2"]
    assert result.open_now_fallback is True
    assert result.open_now_requested is True
    # 退回時要重查、且不能再帶營業條件，否則撈回來的還是空的
    assert repository.calls[-1]["query"] is None


@pytest.mark.asyncio
async def test_open_now_overfetches_candidates():
    """長期性註記等規則仍在應用層判斷，會再濾掉幾家，要多取回一些候選。"""
    facilities = [_facility(f"院所{i}", 100 * (i + 1)) for i in range(20)]
    repository = FakeRepository(facilities)

    await _service(repository).find_nearby_hospitals(
        25.0, 121.0, target_count=5, open_now=True
    )

    assert repository.calls[0]["limit"] == 20  # 5 × 4，未超過上限
    assert repository.calls[0]["limit"] <= OPEN_NOW_OVERFETCH_LIMIT


@pytest.mark.asyncio
async def test_without_open_now_does_not_overfetch_or_filter():
    """省略 open_now 時行為必須與現狀完全一致。"""
    facilities = [
        _facility("關的", 100, open_daytime=False),
        _facility("開的", 200),
    ]
    repository = FakeRepository(facilities)

    result = await _service(repository).find_nearby_hospitals(
        25.0, 121.0, target_count=5
    )

    assert repository.calls[0]["limit"] == 5
    assert repository.calls[0]["query"] is None
    assert [f.name for f in result.facilities] == ["關的", "開的"]
    assert result.open_now_requested is False


@pytest.mark.asyncio
async def test_open_now_combines_with_department_search():
    facilities = [
        _facility("關的內科", 100, open_daytime=False, departments=["內科"]),
        _facility("開的內科", 200, departments=["內科"]),
    ]
    service = _service(FakeRepository(facilities))

    result = await service.find_nearby_facilities_by_department(
        25.0, 121.0, "腸胃科", open_now=True
    )

    assert [f.name for f in result.facilities] == ["開的內科"]
    assert result.match.canonical == "內科"
    assert result.open_now_requested is True
