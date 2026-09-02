"""`/api/medical/*` 的路由層測試。

這兩支端點的路徑不能改：CARE-LIFF 的 `src/api/medicalApi.ts` 已經寫死
`/api/medical/nearby` 與 `/api/medical/facilities`，「附近醫院」整頁都依賴它們。
因此第一個測試斷言的是掛載本身——先前 router 檔案存在但沒有 `include_router`，
整頁在線上是 404，而任何單元測試都沒有察覺。

服務一律以 `app.dependency_overrides` 注入替身（專案規則禁止 monkey patch）。
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.dependencies import CurrentUser, get_current_user, get_medical_service
from app.main import app
from app.schemas import ClinicDaySchedule, ClinicTimeSlot, MedicalFacility
from app.services.medical.department_matcher import DepartmentMatch
from app.services.medical.facility_type_matcher import FacilityTypeMatch
from app.services.medical.medical_service import (
    DepartmentSearchResult,
    NearbySearchResult,
)

client = TestClient(app)


def _facility(
    name: str,
    distance_meters: float | None,
    *,
    departments: list[str] | None = None,
    clinic_time: dict[str, ClinicDaySchedule] | None = None,
    notes: str | None = None,
) -> MedicalFacility:
    return MedicalFacility(
        id=name,
        name=name,
        latitude=25.0,
        longitude=121.5,
        address=f"台北市{name}路 1 號",
        type="醫院",
        distance_meters=distance_meters,
        departments=departments,
        clinic_time=clinic_time,
        notes=notes,
    )


def _all_week(slots: list[ClinicTimeSlot]) -> dict[str, ClinicDaySchedule]:
    return {
        day: ClinicDaySchedule(isClosed=False, slots=slots)
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        )
    }


@pytest.fixture()
def override_current_user():
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        line_user_id="U-test-user"
    )
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def override_medical_service():
    service = AsyncMock()
    app.dependency_overrides[get_medical_service] = lambda: service
    yield service
    app.dependency_overrides.pop(get_medical_service, None)


def test_nearby_route_is_mounted(override_current_user, override_medical_service):
    """掛載本身就是斷言對象：LIFF 打的是這個路徑，沒掛載就是整頁 404。"""
    override_medical_service.find_nearby_hospitals.return_value = NearbySearchResult(
        facilities=[]
    )

    response = client.get("/api/medical/nearby", params={"lat": 25.0, "lng": 121.5})

    assert response.status_code == 200
    body = response.json()
    assert body["facilities"] == []
    assert body["count"] == 0


def test_nearby_keeps_facilities_beyond_first_tier_by_default(
    override_current_user, override_medical_service
):
    """
    這是本次修正的核心：先前 router 固定用 5 公里截掉 service 的結果，
    把「湊不滿就逐級放寬到 50 公里」整個抵銷，導致同一個座標在 LINE 拿得到
    5 家、在 LIFF 卻顯示「附近無資料」。未指定 radius_meters 時不得再截斷。
    """
    override_medical_service.find_nearby_hospitals.return_value = NearbySearchResult(
        facilities=[_facility("遠的但唯一", 18_000)],
        reached_meters=20_000,
        satisfied=False,
    )

    response = client.get("/api/medical/nearby", params={"lat": 25.0, "lng": 121.5})

    body = response.json()
    assert [f["name"] for f in body["facilities"]] == ["遠的但唯一"]
    assert body["reached_meters"] == 20_000
    assert body["expanded"] is True
    assert body["satisfied"] is False
    assert body["furthest_meters"] == 18_000


def test_nearby_truncates_only_when_radius_explicitly_requested(
    override_current_user, override_medical_service
):
    """呼叫端明確要求硬上限時仍須截斷，否則 radius_meters 這個參數形同虛設。"""
    override_medical_service.find_nearby_hospitals.return_value = NearbySearchResult(
        facilities=[
            _facility("近的", 800),
            _facility("剛好在邊界", 1000),
            _facility("太遠的", 1200),
        ],
        reached_meters=5_000,
        satisfied=True,
    )

    response = client.get(
        "/api/medical/nearby",
        params={"lat": 25.0, "lng": 121.5, "radius_meters": 1000},
    )

    body = response.json()
    assert [f["name"] for f in body["facilities"]] == ["近的", "剛好在邊界"]
    assert body["count"] == 2
    assert body["max_meters"] == 1000


def test_nearby_keeps_facilities_without_distance(
    override_current_user, override_medical_service
):
    """距離未知不等於超出半徑——沒有 distance_meters 就無從判斷，不該被濾掉。"""
    override_medical_service.find_nearby_hospitals.return_value = NearbySearchResult(
        facilities=[_facility("距離未知", None)]
    )

    response = client.get(
        "/api/medical/nearby",
        params={"lat": 25.0, "lng": 121.5, "radius_meters": 500},
    )

    assert response.json()["count"] == 1


def test_nearby_forwards_open_now_and_facility_type(
    override_current_user, override_medical_service
):
    """三個過濾維度必須真的往下傳，先前 router 根本沒有這些參數。"""
    override_medical_service.find_nearby_hospitals.return_value = NearbySearchResult(
        facilities=[_facility("某醫院", 400)],
        reached_meters=5_000,
        satisfied=True,
        open_now_requested=True,
        facility_type_match=FacilityTypeMatch(category="醫院", requested="大醫院"),
    )

    response = client.get(
        "/api/medical/nearby",
        params={
            "lat": 25.0,
            "lng": 121.5,
            "open_now": True,
            "facility_type": "大醫院",
        },
    )

    kwargs = override_medical_service.find_nearby_hospitals.await_args.kwargs
    assert kwargs["open_now"] is True
    assert kwargs["facility_type"] == "大醫院"

    body = response.json()
    assert body["facility_type"] == {
        "requested": "大醫院",
        "category": "醫院",
        "is_alias": True,
    }
    assert body["open_now_requested"] is True


def test_nearby_routes_to_department_search_when_department_given(
    override_current_user, override_medical_service
):
    """帶科別要走另一支 service 方法，並把別名對應原樣回傳供前端揭露。"""
    override_medical_service.find_nearby_facilities_by_department.return_value = (
        DepartmentSearchResult(
            facilities=[_facility("腸胃科診所", 900)],
            reached_meters=5_000,
            satisfied=True,
            match=DepartmentMatch(canonical="內科", requested="腸胃科"),
        )
    )

    response = client.get(
        "/api/medical/nearby",
        params={"lat": 25.0, "lng": 121.5, "department": "腸胃科"},
    )

    override_medical_service.find_nearby_hospitals.assert_not_awaited()
    body = response.json()
    assert body["department"] == {
        "requested": "腸胃科",
        "canonical": "內科",
        "is_alias": True,
    }
    assert body["unresolved_department"] is None


def test_nearby_reports_unresolved_department_as_200(
    override_current_user, override_medical_service
):
    """
    科別看不懂是「查詢結果的一種」，不是呼叫端把 API 用錯。回 4xx 會逼前端把它
    跟「Atlas 掛了」混在同一個錯誤橫幅裡，使用者無從得知系統其實沒聽懂哪一科。
    """
    override_medical_service.find_nearby_facilities_by_department.return_value = (
        DepartmentSearchResult(match=None)
    )

    response = client.get(
        "/api/medical/nearby",
        params={"lat": 25.0, "lng": 121.5, "department": "宇宙科"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["unresolved_department"] == "宇宙科"
    assert body["facilities"] == []


def test_nearby_reports_unresolved_facility_type(
    override_current_user, override_medical_service
):
    override_medical_service.find_nearby_hospitals.return_value = NearbySearchResult(
        facility_type_unresolved=True
    )

    response = client.get(
        "/api/medical/nearby",
        params={"lat": 25.0, "lng": 121.5, "facility_type": "宇宙站"},
    )

    assert response.status_code == 200
    assert response.json()["unresolved_facility_type"] == "宇宙站"


def test_nearby_exposes_pharmacy_data_gap(
    override_current_user, override_medical_service
):
    """
    藥局只收錄 116 家，「查到 5 家但最近一家在 18 公里外」看起來完全正常，
    實際是資料缺口。LINE 會講這件事，LIFF 也必須拿得到同一個事實。
    """
    override_medical_service.find_nearby_hospitals.return_value = NearbySearchResult(
        facilities=[_facility("遠方藥局", 18_000)],
        reached_meters=20_000,
        satisfied=True,
        facility_type_match=FacilityTypeMatch(category="藥局", requested="藥局"),
    )

    response = client.get(
        "/api/medical/nearby",
        params={"lat": 25.0, "lng": 121.5, "facility_type": "藥局"},
    )

    assert response.json()["pharmacy_data_gap_meters"] == 18_000


def test_nearby_includes_business_status_and_full_facility_fields(
    override_current_user, override_medical_service
):
    """
    clinic_time / departments / notes 本來就在查詢結果裡，先前被 response model
    帶出去卻沒人用；營業狀態則是 LINE 卡片有、LIFF 完全沒有的資訊。
    """
    override_medical_service.find_nearby_hospitals.return_value = NearbySearchResult(
        facilities=[
            _facility(
                "有急診的醫院",
                500,
                departments=["內科", "急診醫學科"],
                clinic_time=_all_week([ClinicTimeSlot(open="08:00", close="12:00")]),
                notes="如需看診請先電話洽詢",
            )
        ],
        reached_meters=5_000,
        satisfied=True,
    )

    response = client.get("/api/medical/nearby", params={"lat": 25.0, "lng": 121.5})

    facility = response.json()["facilities"][0]
    assert facility["departments"] == ["內科", "急診醫學科"]
    assert facility["notes"] == "如需看診請先電話洽詢"
    assert facility["clinic_time"]["monday"]["slots"] == [
        {"open": "08:00", "close": "12:00"}
    ]
    # 設有急診是能力標示，不該壓掉門診狀態——兩者必須並存。
    assert facility["business_status"]["has_emergency"] is True
    assert facility["business_status"]["status"] != "emergency"


def test_nearby_returns_503_when_service_fails(
    override_current_user, override_medical_service
):
    """底層查詢失敗不該把例外原樣噴給前端。"""
    override_medical_service.find_nearby_hospitals.side_effect = RuntimeError("Atlas 掛了")

    response = client.get("/api/medical/nearby", params={"lat": 25.0, "lng": 121.5})

    assert response.status_code == 503


@pytest.mark.parametrize(
    "params",
    [
        {"lat": 91.0, "lng": 121.5},
        {"lat": 25.0, "lng": 181.0},
        {"lat": 25.0, "lng": 121.5, "radius_meters": 50_001},
        {"lat": 25.0, "lng": 121.5, "limit": 0},
    ],
)
def test_nearby_rejects_out_of_range_params(
    override_current_user, override_medical_service, params
):
    response = client.get("/api/medical/nearby", params=params)

    assert response.status_code == 422
    override_medical_service.find_nearby_hospitals.assert_not_awaited()


def test_facilities_search_returns_candidates(
    override_current_user, override_medical_service
):
    """名稱查詢：LIFF 先前完全沒有這條路，搜尋框打的字是直接被丟掉的。"""
    override_medical_service.find_facility_by_name.return_value = (
        [_facility("臺大醫院", 12_000)],
        3,
    )

    response = client.get(
        "/api/medical/facilities",
        params={"keyword": "臺大", "lat": 25.0, "lng": 121.5},
    )

    assert response.status_code == 200
    body = response.json()
    assert [f["name"] for f in body["facilities"]] == ["臺大醫院"]
    assert body["total_count"] == 3
    assert body["facilities"][0]["business_status"]["status"] == "unknown"


def test_facilities_search_ignores_half_a_coordinate(
    override_current_user, override_medical_service
):
    """只給一半座標時不得當作有座標——否則排序規則會與「沒給座標」不同。"""
    override_medical_service.find_facility_by_name.return_value = ([], 0)

    client.get("/api/medical/facilities", params={"keyword": "臺大", "lat": 25.0})

    kwargs = override_medical_service.find_facility_by_name.await_args.kwargs
    assert kwargs["lat"] is None
    assert kwargs["lng"] is None


def test_facilities_search_requires_keyword(
    override_current_user, override_medical_service
):
    response = client.get("/api/medical/facilities", params={"keyword": ""})

    assert response.status_code == 422
    override_medical_service.find_facility_by_name.assert_not_awaited()
