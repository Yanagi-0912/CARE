"""
`open_now` 過濾行為。

最重要的一條是「急診不得被排除」：clinicTime 記的是門診時間，
依它篩選會在深夜把所有急診醫院藏起來。
"""

import pytest

from app.schemas import ClinicDaySchedule, ClinicTimeSlot, MedicalFacility
from app.services.medical.medical_service import (
    OPEN_NOW_OVERFETCH_LIMIT,
    MedicalService,
)


def _always_open() -> dict[str, ClinicDaySchedule]:
    return {
        key: ClinicDaySchedule(
            isClosed=False, slots=[ClinicTimeSlot(open="00:00", close="23:59")]
        )
        for key in (
            "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday",
        )
    }


def _always_closed() -> dict[str, ClinicDaySchedule]:
    return {
        key: ClinicDaySchedule(isClosed=True, slots=[])
        for key in (
            "monday", "tuesday", "wednesday", "thursday",
            "friday", "saturday", "sunday",
        )
    }


def _facility(
    name: str,
    distance_meters: float,
    *,
    open_always: bool = True,
    departments: list[str] | None = None,
) -> MedicalFacility:
    return MedicalFacility(
        id=f"id-{name}",
        name=name,
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type="西醫診所",
        clinic_time=_always_open() if open_always else _always_closed(),
        departments=departments,
        distance_meters=distance_meters,
    )


class FakeRepository:
    def __init__(self, facilities: list[MedicalFacility]) -> None:
        self._facilities = facilities
        self.calls: list[dict] = []

    async def find_near(self, lat, lng, radius_meters, limit, query=None):
        self.calls.append({"limit": limit, "query": query})
        return [
            f for f in self._facilities if (f.distance_meters or 0) <= radius_meters
        ][:limit]


@pytest.mark.asyncio
async def test_open_now_filters_out_closed_facilities():
    facilities = [
        _facility("關的1", 100, open_always=False),
        _facility("開的1", 200),
        _facility("關的2", 300, open_always=False),
        _facility("開的2", 400),
    ]
    service = MedicalService(repository=FakeRepository(facilities))

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
        _facility("一般診所", 100, open_always=False),
        _facility("急診醫院", 200, open_always=False, departments=["內科", "急診醫學科"]),
    ]
    service = MedicalService(repository=FakeRepository(facilities))

    result = await service.find_nearby_hospitals(25.0, 121.0, open_now=True)

    assert [f.name for f in result.facilities] == ["急診醫院"]
    assert result.open_now_fallback is False


@pytest.mark.asyncio
async def test_falls_back_when_nothing_is_open():
    """深夜全部休診時退回未過濾結果，而非回「查無院所」。"""
    facilities = [
        _facility("關的1", 100, open_always=False),
        _facility("關的2", 200, open_always=False),
    ]
    service = MedicalService(repository=FakeRepository(facilities))

    result = await service.find_nearby_hospitals(25.0, 121.0, open_now=True)

    assert [f.name for f in result.facilities] == ["關的1", "關的2"]
    assert result.open_now_fallback is True
    assert result.open_now_requested is True


@pytest.mark.asyncio
async def test_open_now_overfetches_candidates():
    """營業判斷在應用層做，必須先多取回候選才有東西可篩。"""
    facilities = [_facility(f"院所{i}", 100 * (i + 1)) for i in range(20)]
    repository = FakeRepository(facilities)
    service = MedicalService(repository=repository)

    await service.find_nearby_hospitals(25.0, 121.0, target_count=5, open_now=True)

    assert repository.calls[0]["limit"] == 20  # 5 × 4，未超過上限
    assert repository.calls[0]["limit"] <= OPEN_NOW_OVERFETCH_LIMIT


@pytest.mark.asyncio
async def test_without_open_now_does_not_overfetch_or_filter():
    """省略 open_now 時行為必須與現狀完全一致。"""
    facilities = [
        _facility("關的", 100, open_always=False),
        _facility("開的", 200),
    ]
    repository = FakeRepository(facilities)
    service = MedicalService(repository=repository)

    result = await service.find_nearby_hospitals(25.0, 121.0, target_count=5)

    assert repository.calls[0]["limit"] == 5
    assert [f.name for f in result.facilities] == ["關的", "開的"]
    assert result.open_now_requested is False


@pytest.mark.asyncio
async def test_open_now_combines_with_department_search():
    facilities = [
        _facility("關的內科", 100, open_always=False, departments=["內科"]),
        _facility("開的內科", 200, departments=["內科"]),
    ]
    service = MedicalService(repository=FakeRepository(facilities))

    result = await service.find_nearby_facilities_by_department(
        25.0, 121.0, "腸胃科", open_now=True
    )

    assert [f.name for f in result.facilities] == ["開的內科"]
    assert result.match.canonical == "內科"
    assert result.open_now_requested is True
