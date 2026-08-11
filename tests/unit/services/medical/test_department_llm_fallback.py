"""MedicalService 的兩層解析：關鍵字表優先，表查不到才動用 LLM 兜底。"""

import pytest

from app.schemas import MedicalFacility
from app.services.medical.medical_service import MedicalService


def _facility(name: str) -> MedicalFacility:
    return MedicalFacility(
        id=f"id-{name}",
        name=name,
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type="醫院",
        departments=["外科"],
        distance_meters=1_000,
    )


class FakeRepository:
    def __init__(self) -> None:
        self.queries: list[dict | None] = []

    async def find_near(self, lat, lng, radius_meters, limit, query=None):
        self.queries.append(query)
        return [_facility(f"院所{i}") for i in range(5)]


class FakeResolver:
    """假的 LLM 兜底解析器，記錄被問過哪些詞。"""

    def __init__(self, answer: str | None) -> None:
        self._answer = answer
        self.asked: list[str] = []

    async def resolve(self, text: str) -> str | None:
        self.asked.append(text)
        return self._answer


@pytest.mark.asyncio
async def test_table_hit_never_calls_llm():
    """絕大多數流量都該走表，維持 0 延遲。這是整個設計的前提，必須釘住。"""
    resolver = FakeResolver("內科")
    service = MedicalService(
        repository=FakeRepository(), department_resolver=resolver
    )

    result = await service.find_nearby_facilities_by_department(25.0, 121.0, "腸胃科")

    assert result.match.canonical == "內科"
    assert result.match.source == "table"
    assert resolver.asked == []


@pytest.mark.asyncio
async def test_table_miss_falls_back_to_llm():
    resolver = FakeResolver("外科")
    repository = FakeRepository()
    service = MedicalService(repository=repository, department_resolver=resolver)

    result = await service.find_nearby_facilities_by_department(
        25.0, 121.0, "腹腔鏡科"
    )

    assert resolver.asked == ["腹腔鏡科"]
    assert result.match.canonical == "外科"
    assert result.match.requested == "腹腔鏡科"
    assert result.match.source == "llm"
    # 兜底解析出來的科別要真的拿去查 DB，而不是只影響文案
    assert repository.queries[0] == {"departments": {"$regex": "外科", "$options": "i"}}


@pytest.mark.asyncio
async def test_llm_also_unknown_keeps_honest_failure():
    """兩層都解析不出來時仍回 match=None，維持「我看不懂」而非搜全部科別。"""
    repository = FakeRepository()
    service = MedicalService(
        repository=repository, department_resolver=FakeResolver(None)
    )

    result = await service.find_nearby_facilities_by_department(
        25.0, 121.0, "隨便什麼科"
    )

    assert result.match is None
    assert result.facilities == []
    assert repository.queries == []  # 解析失敗不可打 DB


@pytest.mark.asyncio
async def test_without_resolver_behaviour_is_unchanged():
    """沒接兜底時行為與加這層之前完全相同。"""
    service = MedicalService(repository=FakeRepository())

    result = await service.find_nearby_facilities_by_department(25.0, 121.0, "大腸科")
    assert result.match.canonical == "外科"  # hotfix 之後已在表裡

    unknown = await service.find_nearby_facilities_by_department(
        25.0, 121.0, "隨便什麼科"
    )
    assert unknown.match is None


@pytest.mark.asyncio
async def test_facility_type_falls_back_to_llm():
    resolver = FakeResolver("藥局")
    repository = FakeRepository()
    service = MedicalService(repository=repository, facility_type_resolver=resolver)

    result = await service.find_nearby_hospitals(25.0, 121.0, facility_type="藥妝店")

    assert resolver.asked == ["藥妝店"]
    assert result.facility_type_match.category == "藥局"
    assert result.facility_type_match.source == "llm"
    assert result.facility_type_unresolved is False


@pytest.mark.asyncio
async def test_configure_llm_fallbacks_injects_after_construction():
    """單例在模組載入時就建好，解析器只能事後注入（見 configure_llm_fallbacks）。"""
    resolver = FakeResolver("外科")
    service = MedicalService(repository=FakeRepository())
    service.configure_llm_fallbacks(department_resolver=resolver)

    result = await service.find_nearby_facilities_by_department(
        25.0, 121.0, "腹腔鏡科"
    )

    assert result.match.canonical == "外科"
    assert resolver.asked == ["腹腔鏡科"]
