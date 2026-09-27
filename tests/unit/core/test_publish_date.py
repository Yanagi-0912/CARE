"""`app/core/publish_date.py`：知識庫日期字串的解析與呈現格式。

這些字串是各 scraper 從頁面原樣抄下來的，ETL 端不做正規化，所以同一個欄位裡
本來就混著多種寫法與非日期的值。
"""

from datetime import date

import pytest

from app.core.publish_date import format_publish_date, parse_publish_date


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2024-03-15", date(2024, 3, 15)),
        # scraper_api 把政府 API 的字串原樣寫入，斜線與點都出現過
        ("2024/03/15", date(2024, 3, 15)),
        ("2024.03.15", date(2024, 3, 15)),
        ("2024-3-5", date(2024, 3, 5)),
        # 民國年：庫裡實測 0 筆，但 gov.tw 各頁面的寫法不一致，仍要認得
        ("113-03-15", date(2024, 3, 15)),
    ],
)
def test_parses_the_formats_that_appear_in_the_knowledge_base(raw, expected):
    assert parse_publish_date(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "不詳",
        "未提供",
        "N/A",
        "2024-13-01",  # 不存在的月份
        "2024-02-30",  # 不存在的日
        "2024-03-15T08:30:00Z",  # 時間戳不是發布日
        "民國 113 年 3 月",
    ],
)
def test_returns_none_for_anything_that_is_not_a_date(raw):
    assert parse_publish_date(raw) is None


def test_format_normalizes_every_accepted_form_to_one_display_format():
    """同一張卡片上兩種寫法並陳看起來像資料壞掉。"""
    assert format_publish_date("2024/03/15") == "2024-03-15"
    assert format_publish_date("113-03-15") == "2024-03-15"
    assert format_publish_date("2024-3-5") == "2024-03-05"


def test_format_drops_unparseable_values_instead_of_echoing_them():
    """解析失敗時不退回原字串：印出「不詳 發布」等於宣稱那是發布日期。"""
    assert format_publish_date("不詳") == ""
    assert format_publish_date("2024-03-15T08:30:00Z") == ""
    assert format_publish_date(None) == ""
