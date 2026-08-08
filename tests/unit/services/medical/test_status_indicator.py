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
    _build_status_indicator,
    create_facility_item_box,
)

FT = theme.resolve_theme()


def _render(result: BusinessHoursResult) -> str:
    """把 Flex 結構轉成字串，方便斷言文字是否出現。"""
    return json.dumps(
        _build_status_indicator(result, FT, language="zh-TW"), ensure_ascii=False
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


def test_emergency_hides_next_open_to_avoid_misreading():
    """
    在「設有急診」旁邊放門診時間，會被誤讀成急診那時才開，
    所以急診狀態刻意不顯示下次開診。
    """
    text = _render(
        BusinessHoursResult(
            status=BusinessStatus.EMERGENCY,
            next_open=NextOpen(weekday_key="thursday", time_text="08:00", is_today=False),
        )
    )
    assert "設有急診" in text
    assert "開診" not in text
    assert "24" not in text


def test_note_is_displayed_regardless_of_status():
    text = _render(
        BusinessHoursResult(
            status=BusinessStatus.OPEN, note="春節假期2／17~2／22休診"
        )
    )
    assert "營業中" in text
    assert "春節假期2／17~2／22休診" in text


def test_no_note_block_when_note_absent():
    text = _render(BusinessHoursResult(status=BusinessStatus.OPEN))
    assert "院所註記" not in text


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
