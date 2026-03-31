import pytest
import os
from app.application.medical.medical_service import medical_service
from app.schemas import MedicalFacility


@pytest.mark.asyncio
async def test_find_nearby_hospitals_real_db():

    from app.dependencies import get_mongodb_url
    try:
        get_mongodb_url()
    except ValueError:
        pytest.fail("測試環境缺少 MONGODB_URL")

    # 模擬使用者送出的經緯度 (這組座標使用之前資料庫內的基隆地區範例)
    test_lat = 25.093118
    test_lng = 121.710981

    # 呼叫已經寫好的 Motor 非同步查詢
    facilities = await medical_service.find_nearby_hospitals(
        lat=test_lat,
        lng=test_lng,
        radius_meters=5000,  # 搜 5 公里內
        limit=3,  # 取 3 筆
    )

    # 斷言 1: 是否有正確回傳資料
    assert len(facilities) > 0, "在給定座標 5 公里內找不到任何醫院或資料庫連線失敗"
    assert len(facilities) <= 3, "回傳筆數不應超過 limit 限制"

    # 斷言 2: 首筆資料格式驗證
    first = facilities[0]
    assert isinstance(first, MedicalFacility)
    assert first.name is not None and first.name != "未知名稱"
    assert first.distance_meters is not None
    assert first.distance_meters >= 0.0, "計算出來的距離不應小於 0"
    assert first.type is not None

    # 印出詳細資料供開發人員視覺檢查 (-s 參數)
    print(
        f"\n 測試通過！以 ({test_lat}, {test_lng}) 成功尋找到以下 {len(facilities)} 筆最近的醫院/診所：\n"
    )
    for idx, f in enumerate(facilities, 1):
        print(f" {idx}.  {f.name}")
        print(f"    類型: {f.type}")
        print(f"    距離: {f.distance_meters:.1f} 公尺")
        print(f"    地址: {f.address}")
        print(f"    電話: {f.phone}")
        print("-" * 40)
