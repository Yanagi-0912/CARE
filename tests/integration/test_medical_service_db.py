import pytest
import os
from app.services.medical.medical_service import medical_service
from app.schemas import MedicalFacility


@pytest.mark.integration
@pytest.mark.asyncio
class TestMedicalServiceIntegration:
    """醫療服務整合測試：驗證與真實 MongoDB 的空間查詢 (Geospatial) 功能"""

    @pytest.fixture(autouse=True)
    async def setup_mongodb(self):
        from app.dependencies import get_mongodb_uri
        from app.db.mongodb import MongoDBManager
        import pymongo

        try:
            uri = get_mongodb_uri()
            MongoDBManager._client = None
            MongoDBManager.configure(uri)
        except ValueError:
            pytest.fail("測試環境缺少 MONGODB_URI")

        # 準備測試資料
        collection = MongoDBManager.get_medical_collection()

        # 確保有 2dsphere 索引
        await collection.create_index([("location", pymongo.GEOSPHERE)])

        # 插入一筆測試資料 (位於基隆地區)
        test_doc = {
            "name": "整合測試醫院",
            "latitude": 25.093118,
            "longitude": 121.710981,
            "address": "基隆市測試路 100 號",
            "phone": "02-12345678",
            "type": "醫院",
            "location": {
                "type": "Point",
                "coordinates": [121.710981, 25.093118]
            }
        }

        # 先清理舊的測試資料再插入
        await collection.delete_many({"name": "整合測試醫院"})
        await collection.insert_one(test_doc)

        yield

        # 清理測試資料
        await collection.delete_many({"name": "整合測試醫院"})

    async def test_find_nearby_hospitals_real_db(self):
        # 模擬使用者送出的經緯度 (位於基隆地區)
        test_lat = 25.093118
        test_lng = 121.710981

        # 呼叫已經寫好的 Motor 非同步查詢
        result = await medical_service.find_nearby_hospitals(
            lat=test_lat,
            lng=test_lng,
            target_count=3,  # 取 3 筆
        )
        facilities = result.facilities

        # 斷言 1: 是否有正確回傳資料
        assert len(facilities) > 0, "在給定座標附近找不到任何醫院或資料庫連線失敗"
        assert any(f.name == "整合測試醫院" for f in facilities), "找不到預先插入的整合測試醫院"
        assert len(facilities) <= 3, "回傳筆數不應超過 target_count 限制"

        # 斷言 1b: 分級資訊要能反映實際搜尋範圍
        assert result.reached_meters in (5_000, 10_000, 20_000, 50_000)
        assert result.satisfied is True

        # 斷言 2: 資料格式驗證
        first = facilities[0]
        assert isinstance(first, MedicalFacility)
        assert first.name is not None
        assert first.distance_meters is not None
        assert first.distance_meters >= 0.0

        print(f"\n [Integration Test] 成功尋找到 {len(facilities)} 筆醫院資料。")
