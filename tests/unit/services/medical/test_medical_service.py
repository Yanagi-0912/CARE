from app.schemas import MedicalFacility
from app.services.medical.medical_service import (
    build_tel_uri,
    format_candidate_list,
    format_clinic_time,
    format_facility_detail,
    normalize_facility_name,
)


# ── normalize_facility_name ──────────────────────────────────────────────────

def test_normalize_facility_name_ignores_region_prefix_and_suffix():
    # [Test #3] 確認精確縣市清單能正確剝離縣市 + 鄉鎮市區前綴，不過度刪除機構名
    assert normalize_facility_name("基隆市中正區衛生所") == "中正"
    assert normalize_facility_name("中正診所") == "中正"


def test_normalize_facility_name_explicit_county_prefix_list():
    # [Test #3] 使用精確縣市清單，驗證各類縣市前綴均能正確移除
    assert normalize_facility_name("花蓮縣中正診所") == "中正"
    assert normalize_facility_name("新竹市恩輝診所") == "恩輝"
    assert normalize_facility_name("臺南市安南區衛生所") == "安南"


def test_normalize_facility_name_replaces_tai_with_traditional():
    # [Test #1] 確認「台」字被正規化為「臺」，使查詢關鍵字與正體字資料庫一致。
    # 查詢端會將「臺」展開為 [台臺] 正則，雙向比對兩種字元。
    assert normalize_facility_name("台大醫院") == "臺大"
    assert normalize_facility_name("台北市台安醫院") == "臺安"
    assert normalize_facility_name("台中診所") == "臺中"


# ── format_clinic_time ───────────────────────────────────────────────────────

def test_format_clinic_time_reads_new_slots_schema():
    # [Test #2] 確認 format_clinic_time 正確讀取新 schema（slots 巢狀陣列）。
    # 舊格式 open/close 平行陣列已全部遷移至 slots，不再支援。
    clinic_time = {
        "monday": {
            "isClosed": False,
            "slots": [
                {"start": "09:00", "end": "12:00"},
                {"start": "14:00", "end": "17:30"},
            ],
        },
        "tuesday": {
            "isClosed": True,
            "slots": [],
        },
        "wednesday": {
            "isClosed": False,
            "slots": [],  # slots 存在但為空，應顯示「無資料」
        },
    }
    result = format_clinic_time(clinic_time)
    assert "週一：09:00-12:00、14:00-17:30" in result
    assert "週二：休診" in result
    assert "週三：無資料" in result


def test_format_clinic_time_returns_no_data_when_none():
    # 確認 clinic_time 為 None 時安全回傳「無資料」
    assert format_clinic_time(None) == "無資料"


def test_format_clinic_time_returns_no_data_when_empty():
    # 確認 clinic_time 為空 dict 時安全回傳「無資料」
    assert format_clinic_time({}) == "無資料"


# ── build_tel_uri ─────────────────────────────────────────────────────────────

def test_build_tel_uri_strips_non_digits():
    assert build_tel_uri("(02)24621632") == "tel:0224621632"
    assert build_tel_uri("02-1234-5678") == "tel:0212345678"
    assert build_tel_uri("abc") is None


# ── format_facility_detail ───────────────────────────────────────────────────

def test_format_facility_detail_marks_missing_fields_and_omits_invalid_tel():
    facility = MedicalFacility(
        id="1",
        name="測試診所",
        latitude=25.0,
        longitude=121.0,
        address="台北市測試路 1 號",
        phone=None,
        type="一般診所",
        departments=[],
        clinic_time=None,
    )

    message = format_facility_detail(facility)

    assert "電話：無資料" in message
    assert "營業時間：\n無資料" in message
    assert "診療科別：無資料" in message
    assert "撥打電話：" not in message
    assert "地圖連結：https://www.google.com/maps/search/?api=1&query=25.0%2C121.0" in message


# ── format_candidate_list ────────────────────────────────────────────────────

def test_format_candidate_list_shows_top_three_and_type():
    facilities = [
        MedicalFacility(
            id=str(index),
            name=f"測試醫院{index}",
            latitude=25.0,
            longitude=121.0,
            address=f"測試地址{index}",
            phone="02-12345678",
            type="醫院",
            distance_meters=index * 100,
        )
        for index in range(1, 5)
    ]

    message = format_candidate_list(facilities, total_count=4, has_location=True)

    assert "1. 測試醫院1（距離約 100 公尺）" in message
    assert "類型：醫院" in message
    assert "測試醫院3" in message
    assert "測試醫院4" not in message
    assert "結果超過 3 筆" in message
