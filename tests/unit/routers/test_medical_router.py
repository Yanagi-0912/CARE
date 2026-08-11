"""`/api/medical/nearby` 的路由層測試。

這支端點的路徑不能改：CARE-LIFF 的 `src/api/medicalApi.ts` 已經寫死
`/api/medical/nearby`，「附近醫院」整頁都依賴它。因此第一個測試斷言的是
掛載本身——先前 router 檔案存在但沒有 `include_router`，整頁在線上是 404，
而任何單元測試都沒有察覺。

服務一律以 `app.dependency_overrides` 注入替身（專案規則禁止 monkey patch）。
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.dependencies import CurrentUser, get_current_user, get_medical_service
from app.main import app
from app.schemas import MedicalFacility
from app.services.medical.medical_service import NearbySearchResult

client = TestClient(app)


def _facility(name: str, distance_meters: float | None) -> MedicalFacility:
    return MedicalFacility(
        id=name,
        name=name,
        latitude=25.0,
        longitude=121.5,
        address=f"台北市{name}路 1 號",
        type="醫院",
        distance_meters=distance_meters,
    )


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
    assert response.json() == {"facilities": [], "count": 0}


def test_nearby_filters_out_facilities_beyond_requested_radius(
    override_current_user, override_medical_service
):
    """服務一律搜到 50 公里，但使用者指定的半徑之外不該出現在地圖上。"""
    override_medical_service.find_nearby_hospitals.return_value = NearbySearchResult(
        facilities=[
            _facility("近的", 800),
            _facility("剛好在邊界", 1000),
            _facility("太遠的", 1200),
        ]
    )

    response = client.get(
        "/api/medical/nearby",
        params={"lat": 25.0, "lng": 121.5, "radius_meters": 1000},
    )

    assert response.status_code == 200
    body = response.json()
    assert [f["name"] for f in body["facilities"]] == ["近的", "剛好在邊界"]
    assert body["count"] == 2


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

    assert response.status_code == 200
    assert response.json()["count"] == 1


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
