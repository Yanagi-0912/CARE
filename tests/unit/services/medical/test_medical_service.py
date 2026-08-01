import pytest

from app.schemas import MedicalFacility
from app.services.medical.medical_service import MedicalService


class FakeMedicalFacilityRepository:
    def __init__(self) -> None:
        self.queries: list[dict] = []
        self.near_queries: list[tuple[dict, float, float, int]] = []

    async def find_by_query(self, query: dict, limit: int) -> list[MedicalFacility]:
        self.queries.append({"query": query, "limit": limit})
        return [
            MedicalFacility(
                id="facility-1",
                name="測試院所",
                latitude=25.0,
                longitude=121.0,
                address="測試地址",
                phone="02-12345678",
                type="醫院",
            )
        ]

    async def find_by_query_near(
        self, query: dict, lat: float, lng: float, limit: int
    ) -> list[MedicalFacility]:
        self.near_queries.append((query, lat, lng, limit))
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("keyword", "expected_query"),
    [
        ("馬偕", {"name": {"$regex": "馬偕紀念醫院", "$options": "i"}}),
        ("榮總", {"name": {"$regex": "榮民總醫院", "$options": "i"}}),
        (
            "台北榮總",
            {
                "name": {"$regex": "榮民總醫院", "$options": "i"},
                "address": {"$regex": "臺北", "$options": "i"},
            },
        ),
        (
            "連江醫院",
            {
                "name": {"$regex": "連江縣立醫院", "$options": "i"},
                "address": {"$regex": "連江", "$options": "i"},
                "type": {"$regex": "醫院", "$options": "i"},
            },
        ),
        (
            "澎湖醫院",
            {
                "name": {"$regex": "衛生福利部澎湖醫院", "$options": "i"},
                "address": {"$regex": "澎湖", "$options": "i"},
                "type": {"$regex": "醫院", "$options": "i"},
            },
        ),
        (
            "奇美醫院",
            {
                "name": {"$regex": "奇美醫療", "$options": "i"},
                "type": {"$regex": "醫院", "$options": "i"},
            },
        ),
        (
            "新竹台大醫院",
            {
                "name": {"$regex": "臺灣大學", "$options": "i"},
                "address": {"$regex": "新竹", "$options": "i"},
                "type": {"$regex": "醫院", "$options": "i"},
            },
        ),
        (
            "花蓮中正診所",
            {
                "name": {"$regex": "中正", "$options": "i"},
                "address": {"$regex": "花蓮", "$options": "i"},
                "type": {"$regex": "診所", "$options": "i"},
            },
        ),
        (
            "連江衛生所",
            {
                "name": {"$regex": "衛生所", "$options": "i"},
                "address": {"$regex": "連江", "$options": "i"},
            },
        ),
        (
            "澎湖衛生所",
            {
                "name": {"$regex": "衛生所", "$options": "i"},
                "address": {"$regex": "澎湖", "$options": "i"},
            },
        ),
        (
            "新竹衛生所",
            {
                "name": {"$regex": "衛生所", "$options": "i"},
                "address": {"$regex": "新竹", "$options": "i"},
            },
        ),
        (
            "花蓮醫院",
            {
                "name": {"$regex": "花蓮", "$options": "i"},
                "address": {"$regex": "花蓮", "$options": "i"},
                "type": {"$regex": "醫院", "$options": "i"},
            },
        ),
        (
            "高雄藥局",
            {
                "address": {"$regex": "高雄", "$options": "i"},
                "type": {"$regex": "自營", "$options": "i"},
            },
        ),
        ("衛生所", {"name": {"$regex": "衛生所", "$options": "i"}}),
        ("成大", {"name": {"$regex": "成功大學", "$options": "i"}}),
        ("臺大", {"name": {"$regex": "臺灣大學", "$options": "i"}}),
        ("高醫", {"name": {"$regex": "高雄醫學大學", "$options": "i"}}),
        ("三總", {"name": {"$regex": "三軍總醫院", "$options": "i"}}),
        ("長庚", {"name": {"$regex": "長庚醫療", "$options": "i"}}),
        ("慈濟", {"name": {"$regex": "慈濟醫療", "$options": "i"}}),
    ],
)
async def test_find_facility_by_name_builds_expected_query(
    keyword: str, expected_query: dict
) -> None:
    repository = FakeMedicalFacilityRepository()
    service = MedicalService(repository=repository)

    await service.find_facility_by_name(keyword)

    assert repository.queries == [{"query": expected_query, "limit": 20}]
