"""
深夜沒講「現在有開的」，找附近院所也只列現在能去的。

只在不分科別時自動套用：問牙醫卻跑出急診醫院就答非所問了。
現在是不是深夜由 service 的 is_late_night() 決定，這裡用假 service 直接給答案。
"""

import pytest

from app.i18n.messages import t
from app.schemas import MedicalFacility
from app.services.medical.department_matcher import resolve_department
from app.services.medical.medical_service import (
    DepartmentSearchResult,
    NearbySearchResult,
)
from app.tools import medical_tools

LAT_LNG = {"lat": 25.0, "lng": 121.5}


def _hospital() -> MedicalFacility:
    return MedicalFacility(
        id="id",
        name="急診醫院",
        latitude=25.0,
        longitude=121.5,
        address="測試地址",
        type="綜合醫院",
        departments=["急診醫學科"],
        distance_meters=800,
    )


class _StubMedicalService:
    def __init__(self, *, late_night: bool) -> None:
        self._late_night = late_night
        self.hospitals_calls: list[bool] = []
        self.department_calls: list[bool] = []

    def is_late_night(self) -> bool:
        return self._late_night

    async def find_nearby_hospitals(
        self, lat, lng, target_count=5, open_now=False, facility_type=None
    ) -> NearbySearchResult:
        self.hospitals_calls.append(open_now)
        return NearbySearchResult(
            facilities=[_hospital()],
            reached_meters=5_000,
            satisfied=False,
            open_now_requested=open_now,
        )

    async def find_nearby_facilities_by_department(
        self, lat, lng, department, target_count=5, open_now=False, facility_type=None
    ) -> DepartmentSearchResult:
        self.department_calls.append(open_now)
        return DepartmentSearchResult(
            match=resolve_department(department),
            facilities=[_hospital()],
            reached_meters=5_000,
            satisfied=False,
            open_now_requested=open_now,
        )


@pytest.fixture
def inject_medical_service():
    """注入 stub 後於測試結束時還原，避免污染其他測試對 `_medical_service` 的預期。"""
    original = medical_tools._medical_service

    def _inject(stub: _StubMedicalService) -> _StubMedicalService:
        medical_tools.configure_medical_tools(stub)
        return stub

    yield _inject
    medical_tools.configure_medical_tools(original)


@pytest.mark.asyncio
async def test_late_night_general_search_only_lists_places_open_now(
    inject_medical_service,
):
    """半夜只問「附近有醫院嗎」：列最近的院所等於列一排明天才開的。"""
    stub = inject_medical_service(_StubMedicalService(late_night=True))

    payload = await medical_tools.find_nearby_hospitals.ainvoke(LAT_LNG)

    assert stub.hospitals_calls == [True]
    # 使用者沒要求，要說出來，也要告訴他想找明天看診的該怎麼問
    assert t("location.open_now.late_night_note") in payload


@pytest.mark.asyncio
async def test_daytime_general_search_is_unchanged(inject_medical_service):
    stub = inject_medical_service(_StubMedicalService(late_night=False))

    payload = await medical_tools.find_nearby_hospitals.ainvoke(LAT_LNG)

    assert stub.hospitals_calls == [False]
    assert t("location.open_now.late_night_note") not in payload


@pytest.mark.asyncio
async def test_explicit_open_now_at_night_needs_no_explanation(
    inject_medical_service,
):
    """使用者自己說了要現在有開的，就不必再解釋為什麼只列有開的。"""
    stub = inject_medical_service(_StubMedicalService(late_night=True))

    payload = await medical_tools.find_nearby_hospitals.ainvoke(
        {**LAT_LNG, "open_now": True}
    )

    assert stub.hospitals_calls == [True]
    assert t("location.open_now.late_night_note") not in payload


@pytest.mark.asyncio
async def test_late_night_department_search_is_not_forced(inject_medical_service):
    """問牙醫卻跑出急診醫院就答非所問了：指定科別時照使用者說的找。"""
    stub = inject_medical_service(_StubMedicalService(late_night=True))

    await medical_tools.find_nearby_facilities_by_department.ainvoke(
        {**LAT_LNG, "department": "牙科"}
    )

    assert stub.department_calls == [False]


@pytest.mark.asyncio
async def test_late_night_blank_department_follows_general_rule(
    inject_medical_service,
):
    """科別是空的會退回一般搜尋 —— 那就是沒指定科別，深夜一樣只列現在能去的。"""
    stub = inject_medical_service(_StubMedicalService(late_night=True))

    await medical_tools.find_nearby_facilities_by_department.ainvoke(
        {**LAT_LNG, "department": ""}
    )

    assert stub.hospitals_calls == [True]
    assert stub.department_calls == []
