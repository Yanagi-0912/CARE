# 針對 facility_detail_flex_message的測試。涵蓋營業時間表格、診療科別網格、電話/地圖按鈕的顯示與隱藏邏輯。


from datetime import datetime

import pytest
from app.schemas import MedicalFacility, ClinicDaySchedule, ClinicTimeSlot
from app.services.medical.business_hours import TAIPEI_TZ, WEEKDAY_KEYS
from resources.flex_messages import theme
from resources.flex_messages.medical_messages.facility_detail_flex_message import (
    _HAS_CLINIC_MARK,
    _NO_CLINIC_MARK,
    _TODAY_COLUMN_BG,
    _ZEBRA_COLUMN_BG,
    _build_clinic_time_rows,
    _build_department_grid,
    generate_facility_detail_flex_message,
)

FT = theme.resolve_theme("large")

# 測試門診表：一列是一個時段（早／午／晚診），一欄是一天，
# 首列為表頭、首欄為時段標籤，所以第 n 天的格子是 rows[i]["contents"][n + 1]。


def _make_day(is_closed: bool, slots: list[tuple[str, str]]) -> ClinicDaySchedule:
    return ClinicDaySchedule(
        isClosed=is_closed,
        slots=[ClinicTimeSlot(open=o, close=c) for o, c in slots],
    )


def _full_week(slots: list[tuple[str, str]]) -> dict[str, ClinicDaySchedule]:
    return {day: _make_day(is_closed=False, slots=slots) for day in WEEKDAY_KEYS}


def _today_key() -> str:
    """與實作同一套算法取得今天，測試才不會因為執行當天是星期幾而飄動。"""
    return WEEKDAY_KEYS[datetime.now(TAIPEI_TZ).weekday()]


def _cell(row: dict, day_key: str) -> dict:
    """取出某一天在該列的格子（+1 是跳過最左邊的時段標籤欄）。"""
    return row["contents"][WEEKDAY_KEYS.index(day_key) + 1]


def _texts(node: dict) -> list[str]:
    return [c["text"] for c in node["contents"] if c.get("type") == "text"]


def test_build_clinic_time_rows_empty_returns_empty_list():
    # clinic_time 為 None 時，應回傳空 list（讓呼叫端 fallback 顯示「無資料」）
    assert _build_clinic_time_rows(None, FT) == []


def test_build_clinic_time_rows_all_closed_returns_empty_list():
    # 七天全休診時一個時段都畫不出來，畫一張全是「-」的空表沒有意義，
    # 一樣回空 list 交給呼叫端顯示「無資料」
    clinic_time = {day: _make_day(is_closed=True, slots=[]) for day in WEEKDAY_KEYS}
    assert _build_clinic_time_rows(clinic_time, FT, "zh-TW") == []


def test_build_clinic_time_rows_closed_day_marked_with_dash():
    # 休診那一天在該時段的格子打「-」，有診的天打圓點
    clinic_time = _full_week([("09:00", "12:00")])
    clinic_time["sunday"] = _make_day(is_closed=True, slots=[])

    period_row = _build_clinic_time_rows(clinic_time, FT, "zh-TW")[1]
    assert _texts(_cell(period_row, "sunday")) == [_NO_CLINIC_MARK]
    assert _texts(_cell(period_row, "monday")) == [_HAS_CLINIC_MARK]


def test_build_clinic_time_rows_header_uses_short_weekday_labels():
    # 欄寬只有整表的十分之一，表頭走 weekday.short.*（中文「一」、英文「Mo」）
    rows = _build_clinic_time_rows(_full_week([("09:00", "12:00")]), FT, "zh-TW")
    assert _texts(_cell(rows[0], "monday")) == ["一"]
    assert _texts(_cell(rows[0], "sunday")) == ["日"]


def test_build_clinic_time_rows_localizes_labels():
    # 語言設定要同時作用在表頭星期與時段列名上
    row_str = str(_build_clinic_time_rows(_full_week([("09:00", "12:00")]), FT, "en"))
    assert "Mo" in row_str
    assert "Morning" in row_str
    assert "早診" not in row_str
    assert "週一" not in row_str


def test_build_clinic_time_rows_slots_split_into_period_rows():
    # 同一天的多個時段依開始時間拆進不同的時段列，不再串成一串文字
    clinic_time = _full_week([("09:00", "12:00"), ("14:00", "17:00")])
    rows = _build_clinic_time_rows(clinic_time, FT, "zh-TW")

    assert len(rows) == 3  # 表頭 + 早診 + 午診（整週無晚診，該列不畫）
    assert _texts(rows[1]["contents"][0]) == ["早診", "09:00\n12:00"]
    assert _texts(rows[2]["contents"][0]) == ["午診", "14:00\n17:00"]


def test_build_clinic_time_rows_exception_time_shown_in_cell():
    # 某天時間與該列代表時間不同時（週六提早開診），在那一格圓點下方標出實際時間
    clinic_time = _full_week([("09:30", "12:30")])
    clinic_time["saturday"] = _make_day(is_closed=False, slots=[("09:00", "12:00")])

    period_row = _build_clinic_time_rows(clinic_time, FT, "zh-TW")[1]
    assert _texts(_cell(period_row, "saturday")) == [_HAS_CLINIC_MARK, "09:00\n12:00"]
    # 與代表時間相同的日子只有圓點，不重複標時間
    assert _texts(_cell(period_row, "monday")) == [_HAS_CLINIC_MARK]


def test_build_clinic_time_rows_missing_day_key_marked_with_dash():
    # 只提供一天的資料時，其餘缺失的天數同樣以「-」呈現（資料上與休診無法區分）
    clinic_time = {"monday": _make_day(is_closed=False, slots=[("09:00", "11:00")])}
    rows = _build_clinic_time_rows(clinic_time, FT, "zh-TW")

    assert len(rows) == 2  # 表頭 + 早診
    assert _texts(_cell(rows[1], "monday")) == [_HAS_CLINIC_MARK]
    assert _texts(_cell(rows[1], "tuesday")) == [_NO_CLINIC_MARK]


def test_build_clinic_time_rows_empty_slots_no_valid_range_returns_empty_list():
    # isClosed=False 但 slots 為空：沒有任何有效時段，等同無資料
    clinic_time = {"wednesday": _make_day(is_closed=False, slots=[])}
    assert _build_clinic_time_rows(clinic_time, FT, "zh-TW") == []


def test_build_clinic_time_rows_today_column_highlighted():
    # 今天整欄（含表頭那一格）換成醒目底色，其餘六欄維持一欄白一欄綠
    rows = _build_clinic_time_rows(_full_week([("09:00", "12:00")]), FT, "zh-TW")
    today = _today_key()

    for row in rows:
        assert _cell(row, today)["backgroundColor"] == _TODAY_COLUMN_BG

    others = [day for day in WEEKDAY_KEYS if day != today]
    for day in others:
        expected = _ZEBRA_COLUMN_BG[WEEKDAY_KEYS.index(day) % 2]
        assert _cell(rows[0], day)["backgroundColor"] == expected


def test_build_clinic_time_rows_font_size_scales_text():
    clinic_time = _full_week([("09:00", "17:00")])
    normal = _build_clinic_time_rows(clinic_time, theme.resolve_theme("normal"))
    xlarge = _build_clinic_time_rows(clinic_time, theme.resolve_theme("xlarge"))

    # 表頭的星期字級走 ft.body，隨使用者的字級設定縮放
    assert normal[0]["contents"][1]["contents"][0]["size"] == "md"
    assert xlarge[0]["contents"][1]["contents"][0]["size"] == "xl"


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


def test_build_department_grid_alternates_row_color():
    # chip 底色以「列」為單位交替：一列淺灰、一列米黃，同一列內顏色一致
    departments = ["內科", "外科", "兒科", "婦產科"]
    rows = _build_department_grid(departments, FT)
    first_row_chips = [c for c in rows[0]["contents"] if c.get("type") == "box"]

    assert {chip["backgroundColor"] for chip in first_row_chips} == {"#EEEEEE"}
    assert rows[1]["contents"][0]["backgroundColor"] == "#FFFDE7"


def test_build_department_grid_localizes_known_departments():
    # 部定專科要跟著語言走；字典未收錄的次專科則原樣保留中文
    rows = _build_department_grid(["內科", "腸胃內科"], FT, "en")
    row_str = str(rows)
    assert "Internal Medicine" in row_str
    assert "腸胃內科" in row_str  # 未收錄者退回原文，不得顯示成 key


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
