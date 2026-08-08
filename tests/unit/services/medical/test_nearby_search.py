"""
不分科別的鄰近搜尋，與依科別搜尋共用同一套 5→10→20→50 公里分級。

這組測試的存在意義是防止兩條路徑再度分岔：先前不分科別的搜尋是硬 5 公里，
導致偏鄉使用者問「附近有腸胃科嗎」找得到院所，問「附近有醫院嗎」反而查無資料。
"""

import pytest

from app.schemas import MedicalFacility
from app.services.medical.medical_service import (
    NEARBY_SEARCH_STEPS,
    MedicalService,
)


def _facility(name: str, distance_meters: float) -> MedicalFacility:
    return MedicalFacility(
        id=f"id-{name}",
        name=name,
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type="醫院",
        distance_meters=distance_meters,
    )


class FakeRepository:
    def __init__(self, facilities: list[MedicalFacility]) -> None:
        self._facilities = facilities
        self.calls: list[dict] = []

    async def find_near(self, lat, lng, radius_meters, limit, query=None):
        self.calls.append({"radius_meters": radius_meters, "query": query})
        return [
            f for f in self._facilities if (f.distance_meters or 0) <= radius_meters
        ][:limit]


@pytest.mark.asyncio
async def test_five_km_is_enough_does_not_expand():
    facilities = [_facility(f"院所{i}", 500 * (i + 1)) for i in range(5)]
    service = MedicalService(repository=FakeRepository(facilities))

    result = await service.find_nearby_hospitals(25.0, 121.0)

    assert len(result.facilities) == 5
    assert result.reached_meters == 5_000
    assert result.satisfied is True
    assert result.expanded is False


@pytest.mark.asyncio
async def test_expands_beyond_five_km_when_too_few():
    facilities = [
        _facility("近的", 4_000),
        _facility("遠1", 8_000),
        _facility("遠2", 9_000),
        _facility("遠3", 9_500),
        _facility("遠4", 9_900),
    ]
    service = MedicalService(repository=FakeRepository(facilities))

    result = await service.find_nearby_hospitals(25.0, 121.0)

    assert len(result.facilities) == 5
    assert result.reached_meters == 10_000
    assert result.expanded is True


@pytest.mark.asyncio
async def test_remote_area_returns_partial_instead_of_nothing():
    """這正是先前壞掉的情境：偏鄉 5 公里內只有 1 家衛生所。"""
    service = MedicalService(repository=FakeRepository([_facility("鄉衛生所", 3_200)]))

    result = await service.find_nearby_hospitals(25.0, 121.0)

    assert [f.name for f in result.facilities] == ["鄉衛生所"]
    assert result.satisfied is False
    assert result.reached_meters == NEARBY_SEARCH_STEPS[-1]


@pytest.mark.asyncio
async def test_queries_database_once_without_department_filter():
    facilities = [_facility(f"院所{i}", 500 * (i + 1)) for i in range(5)]
    repository = FakeRepository(facilities)
    service = MedicalService(repository=repository)

    await service.find_nearby_hospitals(25.0, 121.0)

    assert len(repository.calls) == 1
    assert repository.calls[0]["radius_meters"] == NEARBY_SEARCH_STEPS[-1] == 50_000
    assert repository.calls[0]["query"] is None


@pytest.mark.asyncio
async def test_truly_no_facility_within_fifty_km():
    service = MedicalService(repository=FakeRepository([]))

    result = await service.find_nearby_hospitals(25.0, 121.0)

    assert result.facilities == []
    assert result.satisfied is False


@pytest.mark.asyncio
async def test_general_and_department_search_share_the_same_tiers():
    """兩條路徑的分級必須一致，否則又會出現前後矛盾的結果。"""
    facilities = [
        _facility("近的", 4_000),
        _facility("遠1", 18_000),
        _facility("遠2", 18_500),
        _facility("遠3", 19_000),
        _facility("遠4", 19_500),
    ]
    for f in facilities:
        f.departments = ["內科"]

    general = await MedicalService(
        repository=FakeRepository(facilities)
    ).find_nearby_hospitals(25.0, 121.0)
    department = await MedicalService(
        repository=FakeRepository(facilities)
    ).find_nearby_facilities_by_department(25.0, 121.0, "腸胃科")

    assert general.reached_meters == department.reached_meters == 20_000
    assert general.satisfied == department.satisfied is True
