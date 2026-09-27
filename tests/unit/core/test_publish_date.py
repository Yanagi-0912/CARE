"""`app/core/publish_date.py`：知識庫日期字串的解析與呈現格式。

這些字串是各 scraper 從頁面原樣抄下來的，ETL 端不做正規化，所以同一個欄位裡
本來就混著多種寫法與非日期的值。
"""

from datetime import date

import pytest

from app.core.publish_date import (
    extract_stated_publish_date,
    format_publish_date,
    parse_publish_date,
)


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


@pytest.mark.parametrize(
    "text, expected",
    [
        # 線上實測抓到的四種真實寫法（2026-09-27）
        ("衛教內容……\n更新時間：114-01-15", "2025-01-15"),
        ("骨質疏鬆症衛教\n更新：2024-01-08", "2024-01-08"),
        ("食品安全問答\n維護日期：2023/05/01", "2023-05-01"),
        ("新聞稿內容\n發布日期： 2022年3月5日", "2022-03-05"),
    ],
)
def test_extracts_the_date_a_page_labels_as_its_own(text, expected):
    assert extract_stated_publish_date(text) == expected


def test_extraction_ignores_dates_that_are_not_labelled_as_the_pages_date():
    """衛教內文出現的日期多半是別的東西，抓來當發布日只會標錯。"""
    assert extract_stated_publish_date("自 2023-01-01 起健保給付本項目") == ""
    assert extract_stated_publish_date("研究收案期間 2019/01/01 至 2020/12/31") == ""
    assert extract_stated_publish_date("本頁無任何日期") == ""
    assert extract_stated_publish_date("") == ""
    assert extract_stated_publish_date(None) == ""


def test_extraction_takes_the_last_match_because_gov_pages_put_it_in_the_footer():
    text = "更新日期：2020-01-01\n（這是導覽列）\n內文……\n更新日期：2024-06-30"
    assert extract_stated_publish_date(text) == "2024-06-30"


def test_format_drops_unparseable_values_instead_of_echoing_them():
    """解析失敗時不退回原字串：印出「不詳 發布」等於宣稱那是發布日期。"""
    assert format_publish_date("不詳") == ""
    assert format_publish_date("2024-03-15T08:30:00Z") == ""
    assert format_publish_date(None) == ""
