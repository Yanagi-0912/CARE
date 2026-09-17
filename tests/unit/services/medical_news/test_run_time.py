"""`*_TIME` 設定值要在排程器建構時就驗。

以前是在迴圈裡才 `split(":")`：設錯的話 started 的 log 印了、心跳登記了，
然後 task 在第一次醒來前拋 ValueError 靜默死掉——外觀健康、永不執行。
"""

import pytest

from app.services.medical_news.index_scheduler import DrugNewsIndexScheduler
from app.services.medical_news.run_time import parse_run_time


@pytest.mark.parametrize(
    "value, expected",
    [("09:00", (9, 0)), ("00:00", (0, 0)), ("23:59", (23, 59)), (" 3:05 ", (3, 5))],
)
def test_parse_run_time_accepts_hh_mm(value, expected):
    assert parse_run_time(value, setting_name="X") == expected


@pytest.mark.parametrize("value", ["9:00 AM", "25:00", "09:60", "09", "", "09:00:00", "ab:cd"])
def test_parse_run_time_rejects_bad_values_and_names_the_setting(value):
    with pytest.raises(ValueError, match="MEDICAL_NEWS_INDEX_TIME"):
        parse_run_time(value, setting_name="MEDICAL_NEWS_INDEX_TIME")


def test_index_scheduler_validates_run_time_at_construction():
    with pytest.raises(ValueError, match="MEDICAL_NEWS_INDEX_TIME"):
        DrugNewsIndexScheduler(index_service=object(), run_time="03:00 AM")


def test_index_scheduler_next_run_uses_parsed_time():
    from datetime import datetime

    from app.models.medication import TAIPEI_TZ

    scheduler = DrugNewsIndexScheduler(index_service=object(), run_time="03:00")

    next_run = scheduler._next_run_at(datetime(2026, 9, 16, 4, 0, tzinfo=TAIPEI_TZ))

    assert next_run == datetime(2026, 9, 17, 3, 0, tzinfo=TAIPEI_TZ)
