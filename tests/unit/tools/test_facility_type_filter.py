"""工具層 facility_type 參數的標題／文案行為。

測試不 monkey patch 全域的 `_medical_service`，而是透過 `configure_medical_tools()`
注入假的 service（依賴注入），比照 test_range_subtitle.py 的風格。
"""

import pytest

from app.i18n.messages import t
from app.schemas import MedicalFacility
from app.services.medical.department_matcher import DepartmentMatch
from app.services.medical.facility_type_matcher import FacilityTypeMatch
from app.services.medical.medical_service import (
    DepartmentSearchResult,
    NearbySearchResult,
)
from app.tools import medical_tools


def _facility(name: str = "測試院所") -> MedicalFacility:
    return MedicalFacility(
        id="id",
        name=name,
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type="醫院",
        distance_meters=800,
    )


class _StubMedicalService:
    """只實作本測試會呼叫到的兩個方法，回傳事先準備好的結果。"""

    def __init__(
        self,
        *,
        hospitals_result: NearbySearchResult | None = None,
        department_result: DepartmentSearchResult | None = None,
    ) -> None:
        self._hospitals_result = hospitals_result
        self._department_result = department_result
        self.hospitals_calls: list[dict] = []
        self.department_calls: list[dict] = []

    async def find_nearby_hospitals(
        self, lat, lng, target_count=5, open_now=False, facility_type=None
    ) -> NearbySearchResult:
        self.hospitals_calls.append({"open_now": open_now, "facility_type": facility_type})
        return self._hospitals_result

    async def find_nearby_facilities_by_department(
        self, lat, lng, department, target_count=5, open_now=False, facility_type=None
    ) -> DepartmentSearchResult:
        self.department_calls.append(
            {"department": department, "facility_type": facility_type}
        )
        return self._department_result


@pytest.fixture
def inject_medical_service():
    """注入 stub 後於測試結束時還原，避免污染其他測試對 `_medical_service` 的預期。"""
    original = medical_tools._medical_service

    def _inject(stub: _StubMedicalService) -> _StubMedicalService:
        medical_tools.configure_medical_tools(stub)
        return stub

    yield _inject
    medical_tools.configure_medical_tools(original)


# --- find_nearby_hospitals -------------------------------------------------


@pytest.mark.asyncio
async def test_hospitals_title_reflects_resolved_facility_type(inject_medical_service):
    """3.2：有 facility_type_match 時標題要用 location.type.title 帶出類型。"""
    result = NearbySearchResult(
        facilities=[_facility()],
        reached_meters=5_000,
        satisfied=True,
        facility_type_match=FacilityTypeMatch(category="醫院", requested="大醫院"),
    )
    inject_medical_service(_StubMedicalService(hospitals_result=result))

    payload = await medical_tools.find_nearby_hospitals.ainvoke(
        {"lat": 25.0, "lng": 121.0, "facility_type": "大醫院"}
    )

    assert t("location.type.title").format(type="醫院") in payload


@pytest.mark.asyncio
async def test_hospitals_without_facility_type_keeps_default_title(inject_medical_service):
    """向後相容：省略 facility_type 時不覆寫標題，行為與現狀一致。"""
    result = NearbySearchResult(
        facilities=[_facility()], reached_meters=5_000, satisfied=True
    )
    inject_medical_service(_StubMedicalService(hospitals_result=result))

    payload = await medical_tools.find_nearby_hospitals.ainvoke(
        {"lat": 25.0, "lng": 121.0}
    )

    assert t("flex.facility.title.nearby") in payload
    assert t("location.type.title").format(type="醫院") not in payload


@pytest.mark.asyncio
async def test_hospitals_unresolved_facility_type_reports_raw_user_wording(
    inject_medical_service,
):
    """
    3.3：facility_type_match 為 None 時「沒傳類型」與「類型看不懂」無法區分，
    必須看 facility_type_unresolved。看不懂時錯誤訊息要用工具收到的原始參數值，
    不能取自 match（此時 match 本來就是 None，取不到 requested）。
    """
    result = NearbySearchResult(facility_type_unresolved=True)
    inject_medical_service(_StubMedicalService(hospitals_result=result))

    payload = await medical_tools.find_nearby_hospitals.ainvoke(
        {"lat": 25.0, "lng": 121.0, "facility_type": "神秘院所"}
    )

    assert payload == t("location.type.unknown").format(facility_type="神秘院所")


@pytest.mark.asyncio
async def test_hospitals_pharmacy_empty_result_uses_dedicated_message(
    inject_medical_service,
):
    """3.3/3.4：藥局查無結果要用專屬文案，而不是通用的 location.no_facility。"""
    result = NearbySearchResult(
        facilities=[],
        facility_type_match=FacilityTypeMatch(category="藥局", requested="藥局"),
    )
    inject_medical_service(_StubMedicalService(hospitals_result=result))

    payload = await medical_tools.find_nearby_hospitals.ainvoke(
        {"lat": 25.0, "lng": 121.0, "facility_type": "藥局"}
    )

    assert payload == t("location.type.pharmacy_none").format(radius_km="50")


@pytest.mark.asyncio
async def test_hospitals_non_pharmacy_empty_result_keeps_generic_message(
    inject_medical_service,
):
    """對照組：非藥局類型查無結果仍走既有的通用文案，避免誤判分支。"""
    result = NearbySearchResult(
        facilities=[],
        facility_type_match=FacilityTypeMatch(category="醫院", requested="大醫院"),
    )
    inject_medical_service(_StubMedicalService(hospitals_result=result))

    payload = await medical_tools.find_nearby_hospitals.ainvoke(
        {"lat": 25.0, "lng": 121.0, "facility_type": "大醫院"}
    )

    assert payload == t("location.no_facility").format(radius_km="50")


# --- find_nearby_facilities_by_department -----------------------------------


@pytest.mark.asyncio
async def test_department_title_combines_department_and_facility_type(
    inject_medical_service,
):
    """
    3.2：科別與類型同時存在時（例如「大醫院的腸胃科」），標題把兩者併呈
    「附近的腸胃科（醫院）」，讓使用者一眼看出系統同時套用了哪兩個條件。
    """
    result = DepartmentSearchResult(
        match=DepartmentMatch(canonical="腸胃科", requested="腸胃科"),
        facilities=[_facility()],
        reached_meters=5_000,
        satisfied=True,
        facility_type_match=FacilityTypeMatch(category="醫院", requested="大醫院"),
    )
    inject_medical_service(_StubMedicalService(department_result=result))

    payload = await medical_tools.find_nearby_facilities_by_department.ainvoke(
        {
            "lat": 25.0,
            "lng": 121.0,
            "department": "腸胃科",
            "facility_type": "大醫院",
        }
    )

    expected_title = t("location.department.title").format(department="腸胃科（醫院）")
    assert expected_title in payload


@pytest.mark.asyncio
async def test_department_without_facility_type_keeps_department_only_title(
    inject_medical_service,
):
    """向後相容：省略 facility_type 時科別標題維持原樣，不多出括號。"""
    result = DepartmentSearchResult(
        match=DepartmentMatch(canonical="腸胃科", requested="腸胃科"),
        facilities=[_facility()],
        reached_meters=5_000,
        satisfied=True,
    )
    inject_medical_service(_StubMedicalService(department_result=result))

    payload = await medical_tools.find_nearby_facilities_by_department.ainvoke(
        {"lat": 25.0, "lng": 121.0, "department": "腸胃科"}
    )

    assert t("location.department.title").format(department="腸胃科") in payload
    assert "（醫院）" not in payload  # 不應多出類型括號


@pytest.mark.asyncio
async def test_department_unresolved_facility_type_reports_raw_user_wording(
    inject_medical_service,
):
    """科別解析成功、類型解析失敗：錯誤訊息要用原始的 facility_type 參數值。"""
    result = DepartmentSearchResult(
        match=DepartmentMatch(canonical="腸胃科", requested="腸胃科"),
        facility_type_unresolved=True,
    )
    inject_medical_service(_StubMedicalService(department_result=result))

    payload = await medical_tools.find_nearby_facilities_by_department.ainvoke(
        {
            "lat": 25.0,
            "lng": 121.0,
            "department": "腸胃科",
            "facility_type": "神秘院所",
        }
    )

    assert payload == t("location.type.unknown").format(facility_type="神秘院所")


@pytest.mark.asyncio
async def test_department_pharmacy_empty_result_uses_dedicated_message(
    inject_medical_service,
):
    """3.4：科別＋藥局類型同時查無結果時，仍要用藥局專屬文案而非科別查無文案。"""
    result = DepartmentSearchResult(
        match=DepartmentMatch(canonical="家醫科", requested="家醫科"),
        facilities=[],
        facility_type_match=FacilityTypeMatch(category="藥局", requested="藥局"),
    )
    inject_medical_service(_StubMedicalService(department_result=result))

    payload = await medical_tools.find_nearby_facilities_by_department.ainvoke(
        {
            "lat": 25.0,
            "lng": 121.0,
            "department": "家醫科",
            "facility_type": "藥局",
        }
    )

    assert payload == t("location.type.pharmacy_none").format(radius_km="50")


@pytest.mark.asyncio
async def test_department_unknown_department_short_circuits_before_facility_type(
    inject_medical_service,
):
    """
    已知限制（Task 2 揭露）：科別與類型同時解析失敗時只回報科別失敗。
    這裡驗證工具層沒有偷偷去補類型檢查，維持與 service 層一致的行為。
    """
    result = DepartmentSearchResult(match=None, facility_type_unresolved=True)
    inject_medical_service(_StubMedicalService(department_result=result))

    payload = await medical_tools.find_nearby_facilities_by_department.ainvoke(
        {
            "lat": 25.0,
            "lng": 121.0,
            "department": "神秘科",
            "facility_type": "神秘院所",
        }
    )

    assert payload == t("location.department.unknown").format(department="神秘科")
