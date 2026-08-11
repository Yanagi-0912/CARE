"""狀態標籤的 Flex 渲染：下次開診文字、註記顯示、急診的特殊處理。"""

import json

from app.core.user_language import set_request_language
from app.schemas import ClinicDaySchedule, ClinicTimeSlot, MedicalFacility
from app.services.medical.business_hours import (
    BusinessHoursResult,
    BusinessStatus,
    NextOpen,
)
from resources.flex_messages import theme
from resources.flex_messages.medical_messages.facility_brief_flex_message import (
    _build_status_rows,
    create_facility_item_box,
)


def _render(result: BusinessHoursResult, is_emergency: bool = False) -> str:
    """把 Flex 結構轉成字串，方便斷言文字是否出現。"""
    return json.dumps(
        _build_status_rows(result, is_emergency, language="zh-TW"), ensure_ascii=False
    )


def test_open_status_has_no_next_open_line():
    text = _render(BusinessHoursResult(status=BusinessStatus.OPEN))
    assert "營業中" in text
    assert "開診" not in text


def test_break_shows_next_open_today():
    text = _render(
        BusinessHoursResult(
            status=BusinessStatus.BREAK,
            next_open=NextOpen(weekday_key="wednesday", time_text="14:00", is_today=True),
        )
    )
    assert "午休中" in text
    assert "今日 14:00 開診" in text


def test_before_open_shows_next_open_today():
    text = _render(
        BusinessHoursResult(
            status=BusinessStatus.BEFORE_OPEN,
            next_open=NextOpen(weekday_key="wednesday", time_text="08:00", is_today=True),
        )
    )
    assert "今日尚未開診" in text
    assert "今日 08:00 開診" in text


def test_closed_today_shows_weekday_of_next_open():
    text = _render(
        BusinessHoursResult(
            status=BusinessStatus.CLOSED_TODAY,
            next_open=NextOpen(weekday_key="thursday", time_text="08:00", is_today=False),
        )
    )
    assert "今日已結束" in text
    assert "週四 08:00 開診" in text


def test_emergency_is_appended_below_business_status():
    """
    設有急診不再霸佔營業狀態那一格：門診狀態照常顯示，急診另起一列排在最後，
    「今日 18:00 開診」才不會被切開、誤讀成急診那時才開。
    """
    block = _build_status_rows(
        BusinessHoursResult(
            status=BusinessStatus.BREAK,
            next_open=NextOpen(weekday_key="wednesday", time_text="18:00", is_today=True),
        ),
        is_emergency=True,
        language="zh-TW",
    )
    rows = block["contents"]
    text = json.dumps(block, ensure_ascii=False)

    assert "午休中" in text
    assert "今日 18:00 開診" in text
    # 急診是最後一列，且用藍色圓點與綠／橘的營業狀態區隔
    assert "設有急診" in json.dumps(rows[-1], ensure_ascii=False)
    assert theme.STATUS_EMERGENCY in json.dumps(rows[-1], ensure_ascii=False)
    # 資料只說有急診科別，沒說開放時間，不得宣稱 24 小時
    assert "24" not in text


def test_no_emergency_row_when_facility_has_no_emergency_department():
    text = _render(BusinessHoursResult(status=BusinessStatus.OPEN))
    assert "設有急診" not in text


def test_emergency_facility_card_shows_clinic_status():
    """
    蘭嶼衛生所情境：departments 申報了急診醫學科，但它有完整門診時間，
    卡片必須照常顯示門診狀態，而不是只剩「設有急診」。
    """
    set_request_language("zh-TW")
    facility = MedicalFacility(
        id="id-lanyu",
        name="臺東縣蘭嶼鄉衛生所",
        latitude=22.057885,
        longitude=121.509746,
        address="臺東縣蘭嶼鄉紅頭村36-1號",
        phone="(089)732575",
        type="一般診所(醫務室)",
        departments=["不分科", "內科", "外科", "婦產科", "急診醫學科", "牙科"],
        clinic_time={
            key: ClinicDaySchedule(
                isClosed=False, slots=[ClinicTimeSlot(open="00:00", close="23:59")]
            )
            for key in (
                "monday", "tuesday", "wednesday", "thursday",
                "friday", "saturday", "sunday",
            )
        },
        distance_meters=500.0,
    )

    rendered = json.dumps(create_facility_item_box(facility), ensure_ascii=False)
    assert "營業中" in rendered
    assert "設有急診" in rendered


def _facility_with_notes(notes: str | None) -> MedicalFacility:
    """只有註記欄位不同的院所，用來驗證註記在卡片上的呈現。"""
    return MedicalFacility(
        id="id-note",
        name="測試診所",
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type="西醫診所",
        notes=notes,
        distance_meters=500.0,
    )


def test_note_is_rendered_at_card_bottom():
    """註記放在整張卡片的最後一個區塊（按鈕之後），不再夾在營業狀態裡。"""
    set_request_language("zh-TW")
    card = create_facility_item_box(_facility_with_notes("春節假期2／17~2／22休診"))

    last_block = json.dumps(card["contents"][-1], ensure_ascii=False)
    assert "春節假期2／17~2／22休診" in last_block

    # 營業狀態區塊本身不得再帶註記，否則同一段文字會出現兩次
    status_block = json.dumps(card["contents"][1], ensure_ascii=False)
    assert "春節假期" not in status_block


def test_no_note_block_when_note_absent():
    """沒有註記的院所完全不加這個區塊，卡片不留空行。"""
    set_request_language("zh-TW")
    card = create_facility_item_box(_facility_with_notes(None))
    assert "院所註記" not in json.dumps(card, ensure_ascii=False)


def test_card_renders_status_for_real_facility():
    """整張卡片層級的煙霧測試，確認接線正確。"""
    set_request_language("zh-TW")
    facility = MedicalFacility(
        id="id-1",
        name="測試診所",
        latitude=25.0,
        longitude=121.0,
        address="測試地址",
        type="西醫診所",
        clinic_time={
            key: ClinicDaySchedule(
                isClosed=False, slots=[ClinicTimeSlot(open="00:00", close="23:59")]
            )
            for key in (
                "monday", "tuesday", "wednesday", "thursday",
                "friday", "saturday", "sunday",
            )
        },
        notes="如需看診請先電話洽詢",
        distance_meters=500.0,
    )

    rendered = json.dumps(create_facility_item_box(facility), ensure_ascii=False)

    # 長期性註記（不含日期）應降級為請電洽，且註記原文一併顯示
    assert "請先電話洽詢" in rendered
    assert "測試診所" in rendered
