import pytest

from app.repositories.medical_facility_repository import MedicalFacilityRepository


class EmptyPharmacyRepository:
    """藥局庫的空替身：什麼都查不到。"""

    async def find_near(self, lat, lng, radius_meters, limit, query=None):
        return []

    async def find_by_query(self, query, limit):
        return []

    async def find_by_query_near(self, query, lat, lng, limit, max_distance_meters=None):
        return []

    async def find_by_id(self, facility_id):
        return None

    async def list_all_names(self):
        return set()


@pytest.fixture(autouse=True)
def no_real_pharmacy_collection(monkeypatch):
    """
    單元測試裡沒注入 pharmacy_repository 的 MedicalService 一律拿到空的藥局庫。

    院所查無時會再查藥局庫，只注入院所替身的測試若拿到預設的藥局 repository，
    就會連上 .env 指向的正式資料庫。
    """
    monkeypatch.setattr(
        MedicalFacilityRepository,
        "for_pharmacies",
        classmethod(lambda cls: EmptyPharmacyRepository()),
    )
