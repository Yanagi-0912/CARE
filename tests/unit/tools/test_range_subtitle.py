"""搜尋範圍副標的文案邏輯。"""

import pytest

from app.i18n.messages import t
from app.schemas import MedicalFacility
from app.services.medical.department_matcher import resolve_department
from app.services.medical.medical_service import (
    DepartmentSearchResult,
    NearbySearchResult,
)
from app.tools.medical_tools import _build_range_subtitle


def _facility(distance_meters: float) -> MedicalFacility:
    return MedicalFacility(
        id="id",
        name="測試院所",
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type="醫院",
        distance_meters=distance_meters,
    )


def test_within_five_km_reports_five_km():
    result = NearbySearchResult(
        facilities=[_facility(500), _facility(900)],
        reached_meters=5_000,
        satisfied=True,
    )
    subtitle = _build_range_subtitle(result)
    assert "5 公里" in subtitle
    assert "擴大" not in subtitle


def test_expanded_reports_actual_furthest_not_tier():
    """
    階梯跳到 50 公里、但最遠院所只有 27.2 公里時，必須講 28 公里。
    講 50 公里會讓使用者高估交通成本。
    """
    result = NearbySearchResult(
        facilities=[_facility(15_240), _facility(27_230)],
        reached_meters=50_000,
        satisfied=True,
    )
    subtitle = _build_range_subtitle(result)
    assert "28 公里" in subtitle
    assert "50 公里" not in subtitle


def test_partial_reports_search_limit():
    """湊不滿時重點是「已經找到這麼遠」，報搜尋上限才有意義。"""
    result = NearbySearchResult(
        facilities=[_facility(3_240)],
        reached_meters=50_000,
        satisfied=False,
    )
    subtitle = _build_range_subtitle(result)
    assert "50 公里" in subtitle
    assert "1" in subtitle


def test_alias_note_appended_only_for_department_alias():
    expanded_result = DepartmentSearchResult(
        matches=(resolve_department("腸胃科"),),
        facilities=[_facility(800)],
        reached_meters=5_000,
        satisfied=True,
    )
    subtitle = _build_range_subtitle(expanded_result)
    assert "腸胃科" in subtitle and "內科" in subtitle

    exact_result = DepartmentSearchResult(
        matches=(resolve_department("內科"),),
        facilities=[_facility(800)],
        reached_meters=5_000,
        satisfied=True,
    )
    assert "※" not in _build_range_subtitle(exact_result)


def test_general_search_has_no_alias_note():
    """不分科別的結果沒有 matches 屬性可用，不得因此爆炸。"""
    result = NearbySearchResult(
        facilities=[_facility(800)], reached_meters=5_000, satisfied=True
    )
    assert "※" not in _build_range_subtitle(result)


def test_open_now_with_only_emergency_facilities_says_so():
    """
    深夜要求營業中時多半只剩急診。副標講「找到 N 間目前營業中」會和卡片上的
    「今日已結束」打架，資料也沒說急診幾點開。
    """
    hospital = _facility(1_100).model_copy(update={"departments": ["急診醫學科"]})
    result = NearbySearchResult(
        facilities=[hospital],
        reached_meters=5_000,
        satisfied=False,
        open_now_requested=True,
    )

    subtitle = _build_range_subtitle(result)

    assert "設有急診" in subtitle
    assert "營業中" not in subtitle


def test_open_now_with_an_open_clinic_keeps_open_wording():
    clinic = _facility(300).model_copy(update={"type": "西醫診所"})
    hospital = _facility(1_100).model_copy(update={"departments": ["急診醫學科"]})
    result = NearbySearchResult(
        facilities=[clinic, hospital],
        reached_meters=5_000,
        satisfied=False,
        open_now_requested=True,
    )

    assert "營業中" in _build_range_subtitle(result)
def _clinic(facility_id: str) -> MedicalFacility:
    return MedicalFacility(
        id=facility_id,
        name="巷口診所",
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type="診所",
        departments=["不分科"],
        distance_meters=300,
    )


def test_all_unspecified_explains_why_none_lists_the_department():
    """實測回報：搜內科，附近五家全是沒登記專科的診所，副標要講為什麼沒有內科。"""
    result = DepartmentSearchResult(
        matches=(resolve_department("內科"),),
        facilities=[_clinic("a"), _clinic("b")],
        reached_meters=5_000,
        satisfied=True,
        unspecified_ids=frozenset({"a", "b"}),
    )
    subtitle = _build_range_subtitle(result)
    assert t("location.department.all_unspecified").format(
        count=2, department="內科"
    ) in subtitle


def test_mixed_list_has_no_all_unspecified_note():
    """列表裡有一家登記內科就不能說「都沒有內科」。"""
    result = DepartmentSearchResult(
        matches=(resolve_department("內科"),),
        facilities=[_clinic("a"), _clinic("b")],
        reached_meters=5_000,
        satisfied=True,
        unspecified_ids=frozenset({"a"}),
    )
    assert "都沒有登記" not in _build_range_subtitle(result)
