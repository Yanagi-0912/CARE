# 這個檔案在測試:幫我搜尋附近醫院跟找到多筆結果時，要回傳flex message
import pytest
from urllib.parse import parse_qs, urlparse
from app.schemas import MedicalFacility
from resources.flex_messages.medical_messages.facility_brief_flex_message import (
    _build_flex_map_uri,
    _build_flex_tel_uri,
    generate_facility_list_flex_message,
)


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

    parsed = urlparse(uri)
    destination = parse_qs(parsed.query)["destination"][0]
    # 優先級:經緯度->名稱->地址
    assert destination == f"{facility.latitude},{facility.longitude}"
    assert uri.startswith("https://www.google.com/maps/dir/?api=1&destination=")


def test_build_flex_map_uri_uses_coordinates_without_address():
    # 無地址但有座標時，應使用座標
    facility = MedicalFacility(
        name="台大醫院",
        address="",
        latitude=25.04,
        longitude=121.51,
        type="CLINIC",
    )
    uri = _build_flex_map_uri(facility)
    destination = parse_qs(urlparse(uri).query)["destination"][0]
    assert destination == f"{facility.latitude},{facility.longitude}"


def test_build_flex_map_uri_falls_back_to_name():
    # 無座標、無地址，但有名稱時，應使用名稱
    facility = MedicalFacility(
        name="台大醫院",
        address="",
        latitude=0.0,
        longitude=0.0,
        type="CLINIC",
    )
    uri = _build_flex_map_uri(facility)
    destination = parse_qs(urlparse(uri).query)["destination"][0]
    assert destination == "台大醫院"


def test_build_flex_map_uri_default_when_all_missing():
    # 全部缺失時，應回傳預設值
    facility = MedicalFacility(
        name="", address="", latitude=0.0, longitude=0.0, type="CLINIC"
    )
    uri = _build_flex_map_uri(facility)
    destination = parse_qs(urlparse(uri).query)["destination"][0]
    assert destination == "醫療院所"


@pytest.mark.parametrize(
    "input_phone, expected_uri",
    [
        ("02-2312-3456", "tel:0223123456"),  # 帶有橫線
        ("(02) 2312 3456", "tel:0223123456"),  # 帶有括號與空白
        ("0912345678", "tel:0912345678"),  # 正常手機
        ("123456", "tel:123456"),  # 剛好 6 位數，應視為有效（邊界值）
        ("12345", "tel:"),  # 剛好 5 位數，應視為無效（邊界值）
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
    assert flex_result["altText"] == "醫療院所查詢結果"

    bubble = flex_result["contents"]
    assert bubble["type"] == "bubble"
    assert bubble["size"] == "giga"

    body_contents = bubble["body"]["contents"]

    # 驗證上方標題與總數文字
    assert body_contents[0]["text"] == "附近醫療院所"
    assert body_contents[1]["text"] == "為您找到附近 2 間醫療院所，點擊查看詳細資訊"

    # 分離出兩間診所的 Box 結構進行精準驗證
    # 只拿 type == "box" 的元素，剛好會對應到兩間診所
    facility_boxes = [c for c in body_contents if c.get("type") == "box"]

    # 轉成字串個別檢查，就不用管巢狀結構有多深
    first_facility_str = str(facility_boxes[0])
    second_facility_str = str(facility_boxes[1])

    # 第一間診所斷言（正常資料）
    assert "健康第一診所" in first_facility_str
    assert "250" in first_facility_str
    assert "撥打電話" in first_facility_str
    assert "前往地圖" in first_facility_str

    # 第二間診所斷言（資料缺失）
    assert "未知名稱" in second_facility_str
    assert "距離未知" in second_facility_str
    assert "暫無地址資訊" in second_facility_str
    assert "前往地圖" in second_facility_str
    assert "撥打電話" not in second_facility_str  # 確保沒電話時，按鈕被徹底隱藏了


def test_generate_facility_list_flex_message_candidate_list():
    # 模擬候選清單情境，total_count > 實際顯示筆數，應附加提示文字
    mock_facilities = [
        MedicalFacility(
            name="恩輝診所",
            address="基隆市...",
            type="CLINIC",
            latitude=25.1,
            longitude=121.7,
        ),
    ]
    flex_result = generate_facility_list_flex_message(mock_facilities, total_count=5)
    body_contents = flex_result["contents"]["body"]["contents"]

    # 候選清單情境下標題與副標應切換
    assert body_contents[0]["text"] == "找到多筆相似院所"
    assert body_contents[1]["text"] == "為您找到 1 間相似院所，點擊查看詳細資訊"

    # total_count(5) > 實際顯示筆數(1)，應附加提示文字
    full_str = str(body_contents)
    assert "結果超過顯示上限" in full_str


def test_generate_facility_list_flex_message_realistic_multi_candidates():
    # 貼近真實情境：真的有多間同名候選院所
    mock_facilities = [
        MedicalFacility(
            name="中正診所",
            address="基隆市中正區...",
            type="CLINIC",
            latitude=25.1,
            longitude=121.7,
        ),
        MedicalFacility(
            name="中正診所",
            address="花蓮縣...",
            type="CLINIC",
            latitude=23.9,
            longitude=121.6,
        ),
    ]
    flex_result = generate_facility_list_flex_message(mock_facilities, total_count=2)
    body_contents = flex_result["contents"]["body"]["contents"]

    assert body_contents[0]["text"] == "找到多筆相似院所"
    assert body_contents[1]["text"] == "為您找到 2 間相似院所，點擊查看詳細資訊"


def test_generate_facility_list_flex_message_candidate_list_no_hint_when_full():
    # total_count 等於實際顯示筆數時，不應出現提示文字
    mock_facilities = [
        MedicalFacility(
            name="恩輝診所",
            address="基隆市...",
            type="CLINIC",
            latitude=25.1,
            longitude=121.7,
        ),
    ]
    flex_result = generate_facility_list_flex_message(mock_facilities, total_count=1)
    full_str = str(flex_result)
    assert "結果超過顯示上限" not in full_str
