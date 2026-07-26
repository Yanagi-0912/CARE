import pytest
from app.schemas import MedicalFacility
from resources.flex_messages.medical_messages.facility_brief_flex_message import (
    _build_flex_map_uri,
    _build_flex_tel_uri,
    generate_facility_list_flex_message,
)

# 針對地圖與電話的基礎邏輯進行單元測試


def test_build_flex_map_uri_with_name():
    # 測試有名字的情況，確認 URL 有被正確 urlencode
    facility = MedicalFacility(
        name="台大醫院",
        address="台北市常德街1號",
        latitude=25.04,
        longitude=121.51,
        type="CLINIC",
    )
    uri = _build_flex_map_uri(facility)

    assert "query=%E5%8F%B0%E5%A4%A7%E9%86%AB%E9%99%A2" in uri
    assert uri.startswith("https://www.google.com/maps/search/?api=1")


@pytest.mark.parametrize(
    "input_phone, expected_uri",
    [
        ("02-2312-3456", "tel:0223123456"),  # 帶有橫線
        ("(02) 2312 3456", "tel:0223123456"),  # 帶有括號與空白
        ("0912345678", "tel:0912345678"),  # 正常手機
        ("", "tel:"),  # 空字串防禦
        (None, "tel:"),  # None 防禦
        ("123", "tel:"),  # 太短的無效號碼
    ],
)
def test_build_flex_tel_uri(input_phone, expected_uri):
    # 驗證電話號碼清洗邏輯是否完全符合預期
    assert _build_flex_tel_uri(input_phone) == expected_uri


def test_generate_facility_list_flex_message():
    # 測試一間正常、一間嚴重缺失資料
    mock_facilities = [
        MedicalFacility(
            name="健康第一診所",
            distance_meters=250.4,
            address="新竹縣關西鎮中山路1號",
            phone="03-5872000",
            latitude=24.79,
            longitude=121.17,
            type="CLINIC",
        ),
        MedicalFacility(
            name="",  # 測試名稱缺失
            distance_meters=None,  # 測試距離未知
            address="",  # 測試地址缺失
            phone="",  # 沒有電話，預期會隱藏電話按鈕
            latitude=0.0,
            longitude=0.0,
            type="CLINIC",
        ),
    ]

    # 執行包裝函數
    flex_result = generate_facility_list_flex_message(mock_facilities)

    # 1. 最外層 LINE 格式規格檢驗
    assert flex_result["type"] == "flex"
    assert flex_result["altText"] == "附近醫療院所查詢結果"

    bubble = flex_result["contents"]
    assert bubble["type"] == "bubble"
    assert bubble["size"] == "giga"

    body_contents = bubble["body"]["contents"]

    # 驗證上方標題與總數文字
    assert body_contents[0]["text"] == "附近醫療院所"
    assert body_contents[1]["text"] == "為您找到附近 2 間醫療院所"

    # 分離出兩間診所的 Box 結構進行精準驗證
    # 只拿 type == "box" 的元素，剛好會對應到兩間診所
    facility_boxes = [c for c in body_contents if c.get("type") == "box"]

    # 轉成字串個別檢查，就不用管巢狀結構有多深
    first_facility_str = str(facility_boxes[0])
    second_facility_str = str(facility_boxes[1])

    # 第一間診所斷言（正常資料）
    assert "健康第一診所" in first_facility_str
    assert "250" in first_facility_str
    assert "📞 撥打電話" in first_facility_str
    assert "前往地圖" in first_facility_str

    # 第二間診所斷言（資料缺失）
    assert "未知名稱" in second_facility_str
    assert "距離未知" in second_facility_str
    assert "暫無地址資訊" in second_facility_str
    assert "前往地圖" in second_facility_str
    assert "📞 撥打電話" not in second_facility_str  # 確保沒電話時，按鈕被徹底隱藏了
