# 針對 facility_detail_flex_message的測試。涵蓋營業時間表格、診療科別網格、電話/地圖按鈕的顯示與隱藏邏輯。


import pytest
from app.schemas import MedicalFacility, ClinicDaySchedule, ClinicTimeSlot
from resources.flex_messages import theme
from resources.flex_messages.medical_messages.facility_detail_flex_message import (
    _build_clinic_time_rows,
    _build_department_grid,
    generate_facility_detail_flex_message,
)

FT = theme.resolve_theme("large")

# 測試看診時間的row


def _make_day(is_closed: bool, slots: list[tuple[str, str]]) -> ClinicDaySchedule:
    return ClinicDaySchedule(
        isClosed=is_closed,
        slots=[ClinicTimeSlot(open=o, close=c) for o, c in slots],
    )


def test_build_clinic_time_rows_empty_returns_empty_list():
    # clinic_time 為 None 時，應回傳空 list（讓呼叫端 fallback 顯示「無資料」）
    assert _build_clinic_time_rows(None, FT) == []


def test_build_clinic_time_rows_closed_day():
    clinic_time = {"monday": _make_day(is_closed=True, slots=[])}
    rows = _build_clinic_time_rows(clinic_time, FT, "zh-TW")
    row_str = str(rows)
    assert "週一" in row_str
    assert "休診" in row_str


def test_build_clinic_time_rows_localizes_weekday_and_closed_label():
    # 語言設定應同時作用在星期標籤與休診字樣上
    clinic_time = {"monday": _make_day(is_closed=True, slots=[])}
    row_str = str(_build_clinic_time_rows(clinic_time, FT, "en"))
    assert "Mon" in row_str
    assert "Closed" in row_str
    assert "週一" not in row_str


def test_build_clinic_time_rows_multiple_slots_joined_by_delimiter():
    # 同一天有多個時段時，應以「、」串接（比照 format_clinic_time 既有慣例）
    clinic_time = {
        "tuesday": _make_day(
            is_closed=False, slots=[("09:00", "12:00"), ("14:00", "17:00")]
        )
    }
    rows = _build_clinic_time_rows(clinic_time, FT)
    row_str = str(rows)
    assert "09:00-12:00、14:00-17:00" in row_str


def test_build_clinic_time_rows_missing_day_key_skipped():
    # 只提供一天的資料時，其餘缺失的天數應直接跳過，不應產生空白列
    clinic_time = {"monday": _make_day(is_closed=False, slots=[("09:00", "17:00")])}
    rows = _build_clinic_time_rows(clinic_time, FT)
    assert len(rows) == 1
    assert all(r.get("type") == "box" for r in rows)


def test_build_clinic_time_rows_zebra_striping_between_days():
    # 七天全滿時應產生 7 列，且底色逐列交替（不再使用分隔線區隔）
    clinic_time = {
        day: _make_day(is_closed=False, slots=[("09:00", "17:00")])
        for day in [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]
    }
    rows = _build_clinic_time_rows(clinic_time, FT)
    assert len(rows) == 7
    assert all(r.get("type") == "box" for r in rows)
    assert rows[0]["backgroundColor"] == theme.BRAND_TINT
    assert rows[1]["backgroundColor"] == theme.SURFACE


def test_build_clinic_time_rows_empty_slots_no_valid_range_shows_no_data():
    # isClosed=False 但 slots 為空，或 slot 內 open/close 為空字串時，應顯示「無資料」而非空白
    clinic_time = {"wednesday": _make_day(is_closed=False, slots=[])}
    rows = _build_clinic_time_rows(clinic_time, FT, "zh-TW")
    assert "無資料" in str(rows)


def test_build_clinic_time_rows_font_size_scales_text():
    clinic_time = {"monday": _make_day(is_closed=False, slots=[("09:00", "17:00")])}
    normal = _build_clinic_time_rows(clinic_time, theme.resolve_theme("normal"))
    xlarge = _build_clinic_time_rows(clinic_time, theme.resolve_theme("xlarge"))
    assert normal[0]["contents"][0]["size"] == "md"
    assert xlarge[0]["contents"][0]["size"] == "xl"


# 建立診療科別的表格


def test_build_department_grid_empty_returns_no_data_text():
    result = _build_department_grid(None, FT, "zh-TW")
    assert len(result) == 1
    assert result[0]["text"] == "無資料"


def test_build_department_grid_full_row_no_filler():
    # 剛好 3 項（DEPARTMENTS_PER_ROW），不應補 filler
    departments = ["內科", "外科", "兒科"]
    rows = _build_department_grid(departments, FT)
    assert len(rows) == 1
    fillers = [c for c in rows[0]["contents"] if c.get("type") == "filler"]
    assert len(fillers) == 0


def test_build_department_grid_partial_row_fills_with_filler():
    # 4 項會分成 3+1，第二列應補 2 個 filler 補滿到 3 格
    departments = ["內科", "外科", "兒科", "婦產科"]
    rows = _build_department_grid(departments, FT)
    assert len(rows) == 2
    second_row_fillers = [c for c in rows[1]["contents"] if c.get("type") == "filler"]
    assert len(second_row_fillers) == 2


def test_build_department_grid_many_departments_all_rendered_without_collapse():
    # 目前實作沒有收合邏輯，25 項應全部展開渲染成 chip
    # 注意：若之後恢復草稿中的「其他 N 項」收合設計，此測試需要同步更新
    departments = [f"科別{i}" for i in range(25)]
    rows = _build_department_grid(departments, FT)
    full_str = str(rows)
    assert "科別0" in full_str
    assert "科別24" in full_str
    assert "其他" not in full_str  # 目前版本不收合，確認沒有殘留收合按鈕文字


def test_build_department_grid_uses_single_chip_color():
    # 改版後所有 chip 統一使用品牌淺色底，不再逐列交替色
    departments = ["內科", "外科", "兒科", "婦產科"]
    rows = _build_department_grid(departments, FT)
    assert rows[0]["contents"][0]["backgroundColor"] == theme.BRAND_TINT
    assert rows[1]["contents"][0]["backgroundColor"] == theme.BRAND_TINT


# 測試整合產生完整 Flex Message 的函式


def _base_facility(**overrides) -> MedicalFacility:
    defaults = dict(
        name="恩輝診所",
        address="基隆市中正區中正路1號",
        phone="02-2312-3456",
        type="診所",
        latitude=25.13,
        longitude=121.74,
        clinic_time={"monday": _make_day(is_closed=False, slots=[("09:00", "17:00")])},
        departments=["內科", "外科"],
    )
    defaults.update(overrides)
    return MedicalFacility(**defaults)


def test_generate_facility_detail_flex_message_basic_structure():
    facility = _base_facility()
    result = generate_facility_detail_flex_message(facility)

    assert result["type"] == "flex"
    assert result["altText"] == "恩輝診所詳細資訊"
    assert result["contents"]["type"] == "bubble"
    assert result["contents"]["size"] == "giga"
    assert "body" in result["contents"]
    assert "footer" in result["contents"]


def test_generate_facility_detail_flex_message_phone_button_shown_when_valid():
    facility = _base_facility(phone="02-2312-3456")
    result = generate_facility_detail_flex_message(facility)
    footer_str = str(result["contents"]["footer"])
    assert "撥打電話" in footer_str
    assert "tel:0223123456" in footer_str


def test_generate_facility_detail_flex_message_phone_button_hidden_when_missing():
    # 沒有有效電話時，footer 只該有一個按鈕（前往地圖），不該有撥打電話按鈕
    facility = _base_facility(phone="")
    result = generate_facility_detail_flex_message(facility)
    footer_contents = result["contents"]["footer"]["contents"]
    assert len(footer_contents) == 1
    assert "前往地圖" in str(footer_contents[0])


def test_generate_facility_detail_flex_message_phone_text_not_clickable_when_invalid():
    # 電話欄位純文字顯示時，無效電話不該帶 action（不可點擊）
    facility = _base_facility(phone="123")  # 太短，視為無效
    result = generate_facility_detail_flex_message(facility)
    body_contents = result["contents"]["body"]["contents"]
    phone_block = body_contents[2]  # header, address, phone 依序排列
    assert "action" not in phone_block
    assert phone_block["color"] == "#555555"


def test_generate_facility_detail_flex_message_missing_name_and_address_fallback():
    facility = _base_facility(name="", address="")
    result = generate_facility_detail_flex_message(facility)
    full_str = str(result)
    assert "未知名稱" in full_str
    assert "暫無地址資訊" in full_str
    assert result["altText"] == "醫療院所詳細資訊"  # name 為空時 altText 應 fallback


def test_generate_facility_detail_flex_message_localized_to_english():
    facility = _base_facility()
    result = generate_facility_detail_flex_message(facility, language="en")
    full_str = str(result)
    assert "Opening hours" in full_str
    assert "Directions" in full_str
    assert "營業時間" not in full_str


def test_generate_facility_detail_flex_message_font_size_scales_title():
    facility = _base_facility()
    normal = generate_facility_detail_flex_message(facility, font_size="normal")
    xlarge = generate_facility_detail_flex_message(facility, font_size="xlarge")
    # header box 的第二個元素是院所名稱
    normal_name = normal["contents"]["body"]["contents"][0]["contents"][1]
    xlarge_name = xlarge["contents"]["body"]["contents"][0]["contents"][1]
    assert normal_name["size"] == "xl"
    assert xlarge_name["size"] == "4xl"


def test_generate_facility_detail_flex_message_department_count_label():
    facility = _base_facility(departments=["內科", "外科", "兒科"])
    result = generate_facility_detail_flex_message(facility)
    full_str = str(result)
    assert "診療科別（共 3 項）" in full_str


def test_generate_facility_detail_flex_message_no_clinic_time_shows_no_data():
    facility = _base_facility(clinic_time=None)
    result = generate_facility_detail_flex_message(facility)
    full_str = str(result)
    assert "無資料" in full_str


def test_generate_facility_detail_flex_message_type_label_omitted_when_missing():
    # type 為空字串時，header 不該出現分類標籤區塊（if facility.type 判斷）
    facility = _base_facility(type="")
    result = generate_facility_detail_flex_message(facility)
    header_contents = result["contents"]["body"]["contents"][0]["contents"]
    # 只剩名稱這一個 text 元素，沒有分類標籤
    assert len(header_contents) == 2
