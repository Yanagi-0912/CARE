"""醫療服務整合測試：驗證與真實 MongoDB 的空間查詢（$geoNear）。

只連 CARE_e2e 資料庫的 it_medical_facilities，不碰正式的 CARE_database：舊版直接在
正式庫的 medicalFacilities 寫入再刪除一筆測試醫院，每次跑全套測試都會動到正式資料，
中途斷線時那筆假醫院就會留在正式庫。

需要設定 CARE_E2E_MONGODB_URI 才會執行（同 test_emergency_family_integration），
平常的 ./init.sh 不會連任何資料庫。
"""

import os

import pymongo
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from app.db.mongodb import MongoDBManager
from app.schemas import MedicalFacility
from app.services.medical.medical_service import medical_service

E2E_URI = os.getenv("CARE_E2E_MONGODB_URI", "")
E2E_DB = "CARE_e2e"
COLLECTION = "it_medical_facilities"

# 使用者位置（基隆）。附近放 4 家、30 公里外放 1 家：取 3 筆時應該在 5 公里內湊滿，
# 由近到遠，遠的那家不會被選到。資料全由測試自己放，不依賴正式庫裡剛好有哪些醫院。
USER_LAT, USER_LNG = 25.093118, 121.710981
NEARBY = [
    ("整合測試醫院甲", 25.093118, 121.710981),
    ("整合測試醫院乙", 25.100000, 121.715000),
    ("整合測試醫院丙", 25.110000, 121.720000),
    ("整合測試醫院丁", 25.115000, 121.725000),
]
FAR = ("整合測試醫院遠", 25.040000, 121.420000)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not E2E_URI, reason="未設定 CARE_E2E_MONGODB_URI，略過需要真實 MongoDB 的整合測試"
    ),
]


def _doc(name, lat, lng):
    return {
        "name": name,
        "latitude": lat,
        "longitude": lng,
        "address": "基隆市測試路 100 號",
        "phone": "02-12345678",
        "type": "醫院",
        "location": {"type": "Point", "coordinates": [lng, lat]},
    }


@pytest.fixture
async def e2e_medical_collection(monkeypatch):
    """把院所查詢導到 CARE_e2e 的測試 collection；開始前重建、結束後清空。"""
    client = AsyncIOMotorClient(E2E_URI)
    collection = client[E2E_DB][COLLECTION]
    await collection.drop()
    await collection.create_index([("location", pymongo.GEOSPHERE)])
    await collection.insert_many([_doc(*row) for row in (*NEARBY, FAR)])
    # 院所 repository 每次查詢都透過這個方法取 collection，換掉它就不會碰到正式庫。
    monkeypatch.setattr(MongoDBManager, "get_medical_collection", classmethod(lambda cls: collection))
    yield collection
    await collection.delete_many({})
    client.close()


@pytest.mark.asyncio
async def test_find_nearby_hospitals_real_db(e2e_medical_collection):
    result = await medical_service.find_nearby_hospitals(
        lat=USER_LAT, lng=USER_LNG, target_count=3
    )
    facilities = result.facilities

    # 取 3 筆、由近到遠；30 公里外那家不會被選到。
    assert [f.name for f in facilities] == [name for name, _, _ in NEARBY[:3]]
    distances = [f.distance_meters for f in facilities]
    assert distances == sorted(distances)
    assert distances[0] >= 0.0

    # 5 公里內就湊滿 3 筆，不必放寬範圍。
    assert result.reached_meters == 5_000
    assert result.satisfied is True

    first = facilities[0]
    assert isinstance(first, MedicalFacility)
    assert first.distance_meters is not None


@pytest.mark.asyncio
async def test_widens_the_radius_when_nearby_is_not_enough(e2e_medical_collection):
    """附近只剩 1 家時逐級放寬，找到 30 公里外那家（50 公里那一級）。"""
    await e2e_medical_collection.delete_many({"name": {"$in": [n for n, _, _ in NEARBY[1:]]}})

    result = await medical_service.find_nearby_hospitals(
        lat=USER_LAT, lng=USER_LNG, target_count=2
    )

    assert [f.name for f in result.facilities] == [NEARBY[0][0], FAR[0]]
    assert result.reached_meters == 50_000
