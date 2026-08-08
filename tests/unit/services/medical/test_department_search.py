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
        departments=["內科"],
        distance_meters=distance_meters,
    )


class FakeRepository:
    """記錄 find_near 收到的參數，並回傳預先安排好的院所。"""

    def __init__(self, facilities: list[MedicalFacility]) -> None:
        self._facilities = facilities
        self.calls: list[dict] = []

    async def find_near(self, lat, lng, radius_meters, limit, query=None):
        self.calls.append(
            {
                "lat": lat,
                "lng": lng,
                "radius_meters": radius_meters,
                "limit": limit,
                "query": query,
            }
        )
        return [f for f in self._facilities if (f.distance_meters or 0) <= radius_meters][
            :limit
        ]


@pytest.mark.asyncio
async def test_five_km_is_enough_does_not_expand():
    facilities = [_facility(f"院所{i}", 500 * (i + 1)) for i in range(5)]
    service = MedicalService(repository=FakeRepository(facilities))

    result = await service.find_nearby_facilities_by_department(25.0, 121.0, "腸胃科")

    assert result.match.canonical == "內科"
    assert len(result.facilities) == 5
    assert result.reached_meters == 5_000
    assert result.satisfied is True
    assert result.expanded is False


@pytest.mark.asyncio
async def test_expands_when_five_km_has_too_few():
    """5 公里只有 1 家，其餘落在 20 公里內 → 應回報涵蓋到 20 公里。"""
    facilities = [
        _facility("近的", 3_000),
        _facility("遠1", 12_000),
        _facility("遠2", 15_000),
        _facility("遠3", 18_000),
        _facility("遠4", 19_000),
    ]
    service = MedicalService(repository=FakeRepository(facilities))

    result = await service.find_nearby_facilities_by_department(25.0, 121.0, "腸胃科")

    assert len(result.facilities) == 5
    assert result.reached_meters == 20_000
    assert result.satisfied is True
    assert result.expanded is True


@pytest.mark.asyncio
async def test_returns_partial_when_fifty_km_still_not_enough():
    """50 公里湊不滿目標筆數時，回傳找到的部分而不是空清單。"""
    facilities = [_facility("唯一", 30_000), _facility("另一家", 45_000)]
    service = MedicalService(repository=FakeRepository(facilities))

    result = await service.find_nearby_facilities_by_department(25.0, 121.0, "腸胃科")

    assert len(result.facilities) == 2
    assert result.reached_meters == 50_000
    assert result.satisfied is False


@pytest.mark.asyncio
async def test_queries_database_once_with_max_radius():
    """階梯是應用層分級，DB 只打一次；$geoNear 本來就由近到遠回傳。"""
    facilities = [_facility(f"院所{i}", 1_000 * (i + 1)) for i in range(5)]
    repository = FakeRepository(facilities)
    service = MedicalService(repository=repository)

    await service.find_nearby_facilities_by_department(25.0, 121.0, "腸胃科")

    assert len(repository.calls) == 1
    call = repository.calls[0]
    assert call["radius_meters"] == NEARBY_SEARCH_STEPS[-1] == 50_000
    assert call["query"] == {"departments": {"$regex": "內科", "$options": "i"}}


@pytest.mark.asyncio
async def test_unknown_department_does_not_query_database():
    """解析不出科別時不可退化成搜全部，否則使用者會誤以為系統懂他要的科別。"""
    repository = FakeRepository([_facility("不該回傳", 100)])
    service = MedicalService(repository=repository)

    result = await service.find_nearby_facilities_by_department(
        25.0, 121.0, "宇宙無敵科"
    )

    assert result.match is None
    assert result.facilities == []
    assert repository.calls == []


@pytest.mark.asyncio
async def test_no_facility_within_fifty_km():
    service = MedicalService(repository=FakeRepository([]))

    result = await service.find_nearby_facilities_by_department(25.0, 121.0, "腸胃科")

    assert result.match.canonical == "內科"
    assert result.facilities == []
    assert result.satisfied is False


@pytest.mark.asyncio
async def test_respects_custom_target_count():
    facilities = [_facility(f"院所{i}", 500 * (i + 1)) for i in range(5)]
    service = MedicalService(repository=FakeRepository(facilities))

    result = await service.find_nearby_facilities_by_department(
        25.0, 121.0, "腸胃科", target_count=2
    )

    assert len(result.facilities) == 2
    assert result.satisfied is True
