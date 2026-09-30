"""
藥局搜尋：資料來自藥局庫（medical_facilities_pharmacy），不是 medicalFacilities。

兩個方向都要釘住：找藥局時只查藥局庫；找醫院、診所時藥局庫一次都不能被碰到。
"""

from datetime import datetime

import pytest

from app.schemas import MedicalFacility
from app.services.medical.business_hours import TAIPEI_TZ
from app.services.medical.facility_name_index import indexable_pharmacy_names
from app.services.medical.facility_type_matcher import is_pharmacy_type
from app.services.medical.medical_facility_matcher import (
    build_pharmacy_query,
    is_pharmacy_keyword,
)
from app.services.medical.medical_service import (
    NAME_SEARCH_RADIUS_METERS,
    NEARBY_SEARCH_STEPS,
    MedicalService,
)


def _facility(name: str, type_: str = "醫院", distance: float = 800) -> MedicalFacility:
    return MedicalFacility(
        id=f"id-{name}",
        name=name,
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type=type_,
        departments=[],
        distance_meters=distance,
    )


def _pharmacy(name: str, distance: float = 800) -> MedicalFacility:
    return _facility(name, "藥師自營", distance)


class RecordingRepository:
    """記錄每一次呼叫；名稱查詢依序回傳 name_results 的各段結果。"""

    def __init__(
        self,
        *,
        near: list[MedicalFacility] | None = None,
        name_results: list[list[MedicalFacility]] | None = None,
        by_id: MedicalFacility | None = None,
    ) -> None:
        self._near = near or []
        self._name_results = list(name_results or [])
        self._by_id = by_id
        self.near_calls: list[dict] = []
        self.name_calls: list[dict] = []
        self.id_calls: list[str] = []

    @property
    def total_calls(self) -> int:
        return len(self.near_calls) + len(self.name_calls) + len(self.id_calls)

    async def find_near(self, lat, lng, radius_meters, limit, query=None):
        self.near_calls.append({"radius": radius_meters, "limit": limit, "query": query})
        return list(self._near[:limit])

    def _next_name_result(self) -> list[MedicalFacility]:
        return list(self._name_results.pop(0)) if self._name_results else []

    async def find_by_query(self, query, limit):
        self.name_calls.append({"query": query, "max_distance": "no-geo"})
        return self._next_name_result()

    async def find_by_query_near(self, query, lat, lng, limit, max_distance_meters=None):
        self.name_calls.append({"query": query, "max_distance": max_distance_meters})
        return self._next_name_result()

    async def find_by_id(self, facility_id):
        self.id_calls.append(facility_id)
        return self._by_id


def _service(facilities: RecordingRepository, pharmacies: RecordingRepository, **kwargs):
    return MedicalService(repository=facilities, pharmacy_repository=pharmacies, **kwargs)


# --- 找附近的藥局 ------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("facility_type", ["藥局", "藥房", "藥店", "藥師自營", "藥劑生自營"])
async def test_nearby_pharmacy_reads_pharmacy_collection_only(facility_type):
    facilities = RecordingRepository(near=[_facility("某醫院")])
    pharmacies = RecordingRepository(near=[_pharmacy(f"藥局{i}") for i in range(5)])
    service = _service(facilities, pharmacies)

    result = await service.find_nearby_hospitals(25.0, 121.0, facility_type=facility_type)

    assert facilities.total_calls == 0
    # 藥局庫裡全是藥局，不需要再帶 type 條件。
    assert pharmacies.near_calls == [
        {"radius": NEARBY_SEARCH_STEPS[-1], "limit": 5, "query": None}
    ]
    assert [f.name for f in result.facilities] == [f"藥局{i}" for i in range(5)]
    assert result.facility_type_match.category == "藥局"
    assert result.facility_type_match.requested == facility_type
    assert result.satisfied is True
    assert result.reached_meters == NEARBY_SEARCH_STEPS[0]


@pytest.mark.asyncio
async def test_nearby_pharmacy_widens_like_hospital_search():
    """藥局沿用同一套階梯：5 公里內湊不滿就往外找，並回報實際涵蓋範圍。"""
    pharmacies = RecordingRepository(
        near=[_pharmacy("近", 1_000), _pharmacy("遠", 18_000)]
    )
    service = _service(RecordingRepository(), pharmacies)

    result = await service.find_nearby_pharmacies(25.0, 121.0)

    assert [f.name for f in result.facilities] == ["近", "遠"]
    assert result.satisfied is False
    assert result.reached_meters == NEARBY_SEARCH_STEPS[-1]
    assert result.facility_type_match.category == "藥局"


@pytest.mark.asyncio
async def test_nearby_pharmacy_open_now_filters_in_pharmacy_collection():
    facilities = RecordingRepository()
    pharmacies = RecordingRepository(near=[])
    service = _service(
        facilities,
        pharmacies,
        clock=lambda: datetime(2026, 9, 30, 15, 0, tzinfo=TAIPEI_TZ),
    )

    result = await service.find_nearby_hospitals(
        25.0, 121.0, open_now=True, facility_type="藥局"
    )

    assert facilities.total_calls == 0
    # 第一次帶營業條件；一家都沒開才退回不帶條件的查詢。
    assert pharmacies.near_calls[0]["query"] is not None
    assert pharmacies.near_calls[1]["query"] is None
    assert result.open_now_requested is True
    assert result.open_now_fallback is True


@pytest.mark.asyncio
async def test_llm_resolved_pharmacy_type_reads_pharmacy_collection():
    class Resolver:
        async def resolve(self, text):
            return "藥局"

    facilities = RecordingRepository()
    pharmacies = RecordingRepository(near=[_pharmacy("健安藥局")])
    service = _service(facilities, pharmacies, facility_type_resolver=Resolver())

    result = await service.find_nearby_hospitals(25.0, 121.0, facility_type="藥妝店")

    assert facilities.total_calls == 0
    assert [f.name for f in result.facilities] == ["健安藥局"]
    assert result.facility_type_match.requested == "藥妝店"


@pytest.mark.asyncio
@pytest.mark.parametrize("facility_type", [None, "", "大醫院", "醫院", "診所", "綜合醫院"])
async def test_hospital_and_clinic_search_never_touches_pharmacy_collection(facility_type):
    facilities = RecordingRepository(near=[_facility(f"院所{i}") for i in range(5)])
    pharmacies = RecordingRepository(near=[_pharmacy("不該出現")])
    service = _service(facilities, pharmacies)

    result = await service.find_nearby_hospitals(25.0, 121.0, facility_type=facility_type)

    assert pharmacies.total_calls == 0
    assert len(facilities.near_calls) == 1
    assert [f.name for f in result.facilities] == [f"院所{i}" for i in range(5)]


@pytest.mark.asyncio
async def test_department_search_never_touches_pharmacy_collection():
    facilities = RecordingRepository(near=[_facility(f"院所{i}") for i in range(5)])
    pharmacies = RecordingRepository(near=[_pharmacy("不該出現")])
    service = _service(facilities, pharmacies)

    await service.find_nearby_facilities_by_department(25.0, 121.0, ["腸胃科"])
    await service.find_nearby_facilities_by_department(
        25.0, 121.0, ["腸胃科"], facility_type="診所"
    )

    assert pharmacies.total_calls == 0


@pytest.mark.asyncio
async def test_unknown_facility_type_queries_nothing():
    facilities = RecordingRepository()
    pharmacies = RecordingRepository()
    service = _service(facilities, pharmacies)

    result = await service.find_nearby_hospitals(25.0, 121.0, facility_type="神秘院所")

    assert result.facility_type_unresolved is True
    assert facilities.total_calls == 0
    assert pharmacies.total_calls == 0


# --- 依名稱找藥局 ------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword", ["健安藥局", "健安藥房", "健安藥局在哪"])
async def test_pharmacy_keyword_reads_pharmacy_collection_only(keyword):
    facilities = RecordingRepository(name_results=[[_facility("不該出現")]])
    pharmacies = RecordingRepository(name_results=[[_pharmacy("健安藥局")]])
    service = _service(facilities, pharmacies)

    results, total = await service.find_facility_by_name(keyword, lat=25.0, lng=121.5)

    assert facilities.total_calls == 0
    assert [f.name for f in results] == ["健安藥局"]
    assert total == 1
    assert len(pharmacies.name_calls) == 1


@pytest.mark.asyncio
async def test_pharmacy_name_widens_to_nationwide_when_nothing_nearby():
    pharmacies = RecordingRepository(name_results=[[], [_pharmacy("健安藥局")]])
    service = _service(RecordingRepository(), pharmacies)

    results, total = await service.find_pharmacy_by_name("健安藥局", lat=25.0, lng=121.5)

    assert total == 1
    assert [call["max_distance"] for call in pharmacies.name_calls] == [
        NAME_SEARCH_RADIUS_METERS,
        None,
    ]


@pytest.mark.asyncio
async def test_pharmacy_name_without_coordinates_sorts_by_similarity():
    pharmacies = RecordingRepository(
        name_results=[[_pharmacy("新健安中西藥局"), _pharmacy("健安藥局")]]
    )
    service = _service(RecordingRepository(), pharmacies)

    results, _ = await service.find_facility_by_name("健安藥局")

    assert [call["max_distance"] for call in pharmacies.name_calls] == ["no-geo"]
    assert [f.name for f in results] == ["健安藥局", "新健安中西藥局"]


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword", ["仁愛醫院", "皇家診所", "臺大", "中正衛生所"])
async def test_facility_found_never_touches_pharmacy_collection(keyword):
    facilities = RecordingRepository(name_results=[[_facility("查到的院所")]])
    pharmacies = RecordingRepository(name_results=[[_pharmacy("不該出現")]])
    service = _service(facilities, pharmacies)

    results, total = await service.find_facility_by_name(keyword, lat=25.0, lng=121.5)

    assert pharmacies.total_calls == 0
    assert [f.name for f in results] == ["查到的院所"]
    assert total == 1


@pytest.mark.asyncio
async def test_falls_back_to_pharmacy_when_no_facility_matches():
    """名稱裡沒有「藥局」二字的店（屈臣氏景安門市），院所查無後要在藥局庫找到。"""
    facilities = RecordingRepository(name_results=[[], []])
    pharmacies = RecordingRepository(name_results=[[_pharmacy("屈臣氏景安門市")]])
    service = _service(facilities, pharmacies)

    results, total = await service.find_facility_by_name("屈臣氏", lat=25.0, lng=121.5)

    # 院所先查生活圈、再查全國，兩次都查無才輪到藥局。
    assert len(facilities.name_calls) == 2
    assert [f.name for f in results] == ["屈臣氏景安門市"]
    assert total == 1


@pytest.mark.asyncio
async def test_returns_empty_when_neither_collection_matches():
    service = _service(RecordingRepository(), RecordingRepository())

    assert await service.find_facility_by_name("不存在醫院", lat=25.0, lng=121.5) == ([], 0)
    assert await service.find_facility_by_name("不存在藥局") == ([], 0)


@pytest.mark.asyncio
async def test_blank_keyword_queries_nothing():
    facilities = RecordingRepository()
    pharmacies = RecordingRepository()
    service = _service(facilities, pharmacies)

    assert await service.find_facility_by_name("  ") == ([], 0)
    assert facilities.total_calls == 0
    assert pharmacies.total_calls == 0


# --- 查看詳情 ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_by_id_falls_back_to_pharmacy_collection():
    facilities = RecordingRepository(by_id=None)
    pharmacies = RecordingRepository(by_id=_pharmacy("健安藥局"))
    service = _service(facilities, pharmacies)

    facility = await service.get_facility_by_id("6a9ec236f7e4d9f334e1b30f")

    assert facility.name == "健安藥局"
    assert facilities.id_calls == ["6a9ec236f7e4d9f334e1b30f"]


@pytest.mark.asyncio
async def test_get_by_id_found_in_facilities_skips_pharmacy_collection():
    facilities = RecordingRepository(by_id=_facility("某醫院"))
    pharmacies = RecordingRepository(by_id=_pharmacy("不該出現"))
    service = _service(facilities, pharmacies)

    facility = await service.get_facility_by_id("69be8f50c92d1a1c8fc5b640")

    assert facility.name == "某醫院"
    assert pharmacies.total_calls == 0


@pytest.mark.asyncio
async def test_get_by_id_missing_everywhere_returns_none():
    service = _service(RecordingRepository(), RecordingRepository())

    assert await service.get_facility_by_id("missing") is None


# --- 查詢條件 ----------------------------------------------------------------


def _name(pattern: str) -> dict:
    return {"name": {"$regex": pattern, "$options": "i"}}


@pytest.mark.parametrize(
    ("keyword", "expected_query", "expected_rank_keyword"),
    [
        ("健安藥局", _name("健安"), "健安"),
        ("健安 藥局？", _name("健安"), "健安"),
        # 藥局庫沒有任何名稱含「藥房」，去尾後才比對得到「健安藥局」。
        ("健安藥房", _name("健安"), "健安"),
        # 只剩一個字時補回「藥局」，否則「李」會比對到所有含李的店名。
        ("李藥局", _name("李藥局"), "李"),
        ("李藥房", _name("李藥局"), "李"),
        # 院所的口語對照表不能套在藥局上：「成大藥局」是登記名稱。
        ("成大藥局", _name("成大"), "成大"),
        ("榮總藥局", _name("榮總"), "榮總"),
        # 藥局名稱多半登記成「台」，少數用「臺」，兩種都要比對得到。
        ("臺安藥局", _name("[台臺]安"), "臺安"),
        ("康是美", _name("康是美"), "康是美"),
        ("藥局", _name("藥局"), ""),
        (
            "高雄藥局",
            {"address": {"$regex": "高雄", "$options": "i"}},
            "",
        ),
        (
            "台北藥房",
            {"address": {"$regex": "[台臺]北", "$options": "i"}},
            "",
        ),
        (
            "台中大樹藥局",
            {
                "$or": [
                    _name("[台臺]中大樹"),
                    {
                        **_name("大樹"),
                        "address": {"$regex": "[台臺]中", "$options": "i"},
                    },
                ]
            },
            "臺中大樹",
        ),
        (
            "高雄市李藥局",
            {
                "$or": [
                    _name("高雄市李"),
                    {
                        **_name("李藥局"),
                        "address": {"$regex": "高雄市", "$options": "i"},
                    },
                ]
            },
            "高雄市李",
        ),
        ("", {}, ""),
        ("  ？", {}, ""),
    ],
)
def test_build_pharmacy_query(keyword, expected_query, expected_rank_keyword):
    assert build_pharmacy_query(keyword) == (expected_query, expected_rank_keyword)


@pytest.mark.parametrize(
    ("keyword", "expected"),
    [
        ("健安藥局", True),
        ("健安藥房", True),
        ("藥局", True),
        ("仁愛醫院", False),
        ("皇家診所", False),
        ("屈臣氏", False),
        ("", False),
        (None, False),
    ],
)
def test_is_pharmacy_keyword(keyword, expected):
    assert is_pharmacy_keyword(keyword) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("藥局", True),
        ("藥房", True),
        ("藥店", True),
        ("藥師自營", True),
        ("大醫院", False),
        ("診所", False),
        ("藥妝店", False),
        ("", False),
        (None, False),
    ],
)
def test_is_pharmacy_type(text, expected):
    assert is_pharmacy_type(text) is expected


def test_indexable_pharmacy_names_drops_single_character_names():
    names = {"來藥局", "高藥局", "李藥局", "健安藥局", "康是美三井藥局", "屈臣氏景安門市"}

    assert indexable_pharmacy_names(names) == {
        "健安藥局",
        "康是美三井藥局",
        "屈臣氏景安門市",
    }
