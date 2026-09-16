"""
departments 只有「不分科」的院所，要怎麼被科別搜尋看見。

問題的來源：資料庫有一批院所沒有申報專科，departments 就是 ["不分科"]。科別
查詢是對 departments 下 regex，於是那些院所對任何專科查詢都不會命中——使用者
站在一間只標「不分科」的診所旁邊搜「附近的內科」，得到的是「50 公里內查無」。
那不是附近沒有院所，是資料的申報粒度被當成了臨床事實。

處置分兩段（見 department_matcher 與 MedicalService._supplement_with_unspecified）：
    通科型科別（內科、家醫科）主查詢就一併涵蓋未申報專科的院所。
    專科（眼科、骨科…）維持精確比對，只有湊不滿時才依距離補上——
    一間沒申報科別的診所不等於有那一科，混進去等於把人導去白跑一趟。
兩段列出的不分科院所都要進 unspecified_ids 讓卡片標示（MedicalService._unspecified_ids）：
搜內科時附近若全是不分科診所，「附近的內科」底下五家可以一家都沒寫內科。
"""

import re

import pytest

from app.schemas import MedicalFacility
from app.services.medical.department_matcher import (
    UNSPECIFIED_DEPARTMENTS,
    build_department_query,
)
from app.services.medical.medical_service import DEFAULT_TARGET_COUNT, MedicalService


def _facility(name: str, distance_meters: float, departments: list[str]):
    return MedicalFacility(
        id=f"id-{name}",
        name=name,
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type="診所",
        departments=departments,
        distance_meters=distance_meters,
    )


class QueryAwareRepository:
    """會真的套用 departments regex 的假 repository。

    既有的 FakeRepository 忽略 query，測不出「不分科到底有沒有被撈到」——那正是
    這組測試唯一要驗的事。
    """

    def __init__(self, facilities):
        self._facilities = facilities
        self.calls: list[dict] = []

    async def find_near(self, lat, lng, radius_meters, limit, query=None):
        self.calls.append({"query": query, "limit": limit})
        pattern = ((query or {}).get("departments") or {}).get("$regex")
        matched = [
            f
            for f in self._facilities
            if (f.distance_meters or 0) <= radius_meters
            and (
                pattern is None
                or any(re.search(pattern, d) for d in (f.departments or []))
            )
        ]
        return matched[:limit]


# --- 通科型科別：主查詢就涵蓋 ------------------------------------------------


def test_general_practice_query_covers_unspecified_facilities():
    pattern = build_department_query("內科")["departments"]["$regex"]
    assert all(re.search(pattern, value) for value in UNSPECIFIED_DEPARTMENTS)


def test_specialist_query_stays_exact():
    """眼科的結果混進沒申報科別的診所，等於把人導去白跑一趟。"""
    pattern = build_department_query("眼科")["departments"]["$regex"]
    assert not any(re.search(pattern, value) for value in UNSPECIFIED_DEPARTMENTS)


@pytest.mark.asyncio
async def test_clinic_next_door_is_found_when_searching_internal_medicine():
    """實測回報的情境：人就在只標「不分科」的診所旁邊，搜內科卻查無。"""
    repository = QueryAwareRepository([_facility("巷口診所", 80, ["不分科"])])
    service = MedicalService(repository=repository)

    result = await service.find_nearby_facilities_by_department(25.0, 121.0, ["內科"])

    assert [f.name for f in result.facilities] == ["巷口診所"]
    assert result.unspecified_ids == {"id-巷口診所"}, "列出來了，但卡片要標示它沒寫內科"
    assert len(repository.calls) == 1, "通科型已涵蓋，不該再打第二次 DB"


@pytest.mark.asyncio
async def test_internal_medicine_marks_only_facilities_without_it():
    """實測回報的情境：搜內科，附近的不分科診所排在前面，卡片上看不出為什麼列出它們。"""
    repository = QueryAwareRepository(
        [
            _facility("巷口診所", 80, ["不分科"]),
            _facility("一般診所", 150, ["西醫一般科"]),
            _facility("兩者皆有", 300, ["內科", "不分科"]),
            _facility("醫學中心", 900, ["家醫科、內科、外科"]),
            _facility("內科診所", 1_200, ["內科"]),
        ]
    )
    service = MedicalService(repository=repository)

    result = await service.find_nearby_facilities_by_department(25.0, 121.0, ["內科"])

    assert [f.name for f in result.facilities] == [
        "巷口診所", "一般診所", "兩者皆有", "醫學中心", "內科診所",
    ], "只加標示，搜尋結果與排序不變"
    assert result.unspecified_ids == {"id-巷口診所", "id-一般診所"}


@pytest.mark.asyncio
async def test_unspecified_is_not_marked_when_requested():
    """保底卡本來就在找不分科，不分科院所正是使用者要的，不該被標成未載明科別。"""
    repository = QueryAwareRepository(
        [
            _facility("巷口診所", 80, ["不分科"]),
            _facility("一般診所", 150, ["西醫一般科"]),
        ]
    )
    service = MedicalService(repository=repository)

    result = await service.find_nearby_facilities_by_department(
        25.0, 121.0, ["家醫科", "內科", "不分科"]
    )

    assert len(result.facilities) == 2
    assert result.unspecified_ids == frozenset()


# --- 專科：湊不滿才補，且要標示 ----------------------------------------------


@pytest.mark.asyncio
async def test_specialist_search_supplements_when_short():
    repository = QueryAwareRepository(
        [
            _facility("皮膚科診所", 2_000, ["皮膚科"]),
            _facility("巷口診所", 80, ["不分科"]),
            _facility("一般診所", 300, ["西醫一般科"]),
        ]
    )
    service = MedicalService(repository=repository)

    result = await service.find_nearby_facilities_by_department(25.0, 121.0, ["皮膚科"])

    names = [f.name for f in result.facilities]
    assert names[0] == "皮膚科診所", "正牌專科永遠排在補列的前面"
    assert set(names[1:]) == {"巷口診所", "一般診所"}
    assert result.unspecified_ids == {"id-巷口診所", "id-一般診所"}


@pytest.mark.asyncio
async def test_supplement_does_not_duplicate_the_primary_results():
    """同時申報專科與不分科的院所會被兩次查詢都撈到，不得重複列出。"""
    repository = QueryAwareRepository(
        [_facility("兩者皆有", 500, ["皮膚科", "不分科"])]
    )
    service = MedicalService(repository=repository)

    result = await service.find_nearby_facilities_by_department(25.0, 121.0, ["皮膚科"])

    assert [f.name for f in result.facilities] == ["兩者皆有"]
    assert result.unspecified_ids == frozenset()


@pytest.mark.asyncio
async def test_satisfied_specialist_search_never_supplements():
    """湊得滿就不補：沒申報科別的診所不是專科院所的替代品。"""
    repository = QueryAwareRepository(
        [
            *(
                _facility(f"皮膚科{i}", 500 * (i + 1), ["皮膚科"])
                for i in range(DEFAULT_TARGET_COUNT)
            ),
            _facility("巷口診所", 80, ["不分科"]),
        ]
    )
    service = MedicalService(repository=repository)

    result = await service.find_nearby_facilities_by_department(25.0, 121.0, ["皮膚科"])

    assert "巷口診所" not in [f.name for f in result.facilities]
    assert result.unspecified_ids == frozenset()
    assert len(repository.calls) == 1


@pytest.mark.asyncio
async def test_supplement_keeps_the_result_within_the_target_count():
    repository = QueryAwareRepository(
        [
            _facility("皮膚科診所", 2_000, ["皮膚科"]),
            *(_facility(f"不分科{i}", 100 * (i + 1), ["不分科"]) for i in range(9)),
        ]
    )
    service = MedicalService(repository=repository)

    result = await service.find_nearby_facilities_by_department(25.0, 121.0, ["皮膚科"])

    assert len(result.facilities) == DEFAULT_TARGET_COUNT
