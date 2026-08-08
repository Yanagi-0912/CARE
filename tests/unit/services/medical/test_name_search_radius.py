"""
依名稱查詢時的距離限縮行為。

限縮的目的是避免「仁愛醫院」這種全台數十家同名院所的候選清單被外縣市稀釋；
但限縮不能是硬上限 —— 使用者在高雄問「臺大醫院在哪」是合理需求，
50 公里內查無結果時必須自動放寬為全國搜尋。
"""

import pytest

from app.schemas import MedicalFacility
from app.services.medical.medical_service import (
    NAME_SEARCH_RADIUS_METERS,
    MedicalService,
)


def _facility(name: str) -> MedicalFacility:
    return MedicalFacility(
        id=f"id-{name}",
        name=name,
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type="醫院",
    )


class RecordingRepository:
    """記錄每次 find_by_query_near 的 max_distance_meters，並依序回傳預設結果。"""

    def __init__(self, results_per_call: list[list[MedicalFacility]]) -> None:
        self._results_per_call = results_per_call
        self.max_distances: list[int | None] = []

    async def find_by_query_near(
        self, query, lat, lng, limit, max_distance_meters=None
    ):
        self.max_distances.append(max_distance_meters)
        index = len(self.max_distances) - 1
        if index < len(self._results_per_call):
            return list(self._results_per_call[index])
        return []

    async def find_by_query(self, query, limit):  # pragma: no cover - 本檔不走此路徑
        return []


@pytest.mark.asyncio
async def test_local_hit_does_not_widen_to_nationwide():
    repository = RecordingRepository([[_facility("臺北市立聯合醫院仁愛院區")]])
    service = MedicalService(repository=repository)

    results, total = await service.find_facility_by_name("仁愛醫院", lat=25.0, lng=121.5)

    assert total == 1
    # 只查一次，且帶了生活圈半徑
    assert repository.max_distances == [NAME_SEARCH_RADIUS_METERS]


@pytest.mark.asyncio
async def test_widens_to_nationwide_when_nothing_nearby():
    """高雄使用者查「臺大醫院」：50 公里內沒有，必須放寬而不是回查無資料。"""
    repository = RecordingRepository([[], [_facility("國立臺灣大學醫學院附設醫院")]])
    service = MedicalService(repository=repository)

    results, total = await service.find_facility_by_name("臺大醫院", lat=22.6, lng=120.3)

    assert total == 1
    assert results[0].name == "國立臺灣大學醫學院附設醫院"
    # 第一次帶半徑，第二次不帶（全國搜尋）
    assert repository.max_distances == [NAME_SEARCH_RADIUS_METERS, None]


@pytest.mark.asyncio
async def test_returns_empty_when_nationwide_also_finds_nothing():
    repository = RecordingRepository([[], []])
    service = MedicalService(repository=repository)

    results, total = await service.find_facility_by_name("不存在醫院", lat=25.0, lng=121.5)

    assert results == []
    assert total == 0
    assert repository.max_distances == [NAME_SEARCH_RADIUS_METERS, None]


@pytest.mark.asyncio
async def test_no_coordinates_skips_geo_path_entirely():
    """沒有座標時走純關鍵字查詢，不應觸發任何 geo 查詢。"""
    repository = RecordingRepository([])
    service = MedicalService(repository=repository)

    await service.find_facility_by_name("仁愛醫院")

    assert repository.max_distances == []
