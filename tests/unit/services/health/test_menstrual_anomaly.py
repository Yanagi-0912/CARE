"""``detect_menstrual_anomaly`` 的門檻判定（health-alerts spec「經期異常只
通知本人」；task-5-brief 5.2）。

參數化涵蓋週期 23／24／38／39 天與經期 8／9 天的邊界，以及任一輸入缺席時
不參與判定。純函式，不需要任何 fixture 或替身。
"""

from datetime import date, timedelta

import pytest

from app.services.health.menstrual_anomaly import detect_menstrual_anomaly

START = date(2026, 9, 1)


# ── 週期長度：previous_start 不是 None 時才判定 ──────────────────────────


@pytest.mark.parametrize(
    ("cycle_days", "expected_anomaly"),
    [
        (23, True),  # 過短，異常
        (24, False),  # 正常範圍下界
        (38, False),  # 正常範圍上界
        (39, True),  # 過長，異常
    ],
)
def test_cycle_length_boundaries(cycle_days, expected_anomaly):
    previous_start = START
    start = START + timedelta(days=cycle_days)

    assert (
        detect_menstrual_anomaly(previous_start, start, None) is expected_anomaly
    )


def test_no_previous_start_does_not_trigger_cycle_anomaly():
    """沒有前一筆（最早一筆紀錄）：週期無從判定，SHALL NOT 視為異常。"""
    assert detect_menstrual_anomaly(None, START, None) is False


# ── 經期天數：end 不是 None 時才判定 ─────────────────────────────────────


@pytest.mark.parametrize(
    ("period_days", "expected_anomaly"),
    [
        (8, False),  # 正常範圍上界
        (9, True),  # 過長，異常
    ],
)
def test_period_length_boundaries(period_days, expected_anomaly):
    end = START + timedelta(days=period_days - 1)

    assert detect_menstrual_anomaly(None, START, end) is expected_anomaly


def test_no_end_date_does_not_trigger_period_anomaly():
    """仍在進行中（沒有結束日期）：經期天數無從判定，SHALL NOT 視為異常。"""
    assert detect_menstrual_anomaly(None, START, None) is False


def test_either_half_anomalous_makes_the_whole_record_anomalous():
    """週期正常但經期過長：整體仍視為異常。"""
    previous_start = START
    start = previous_start + timedelta(days=29)  # 正常週期
    end = start + timedelta(days=9)  # 10 天，經期過長

    assert detect_menstrual_anomaly(previous_start, start, end) is True


def test_both_halves_normal_is_not_anomalous():
    previous_start = START
    start = previous_start + timedelta(days=29)
    end = start + timedelta(days=4)  # 5 天

    assert detect_menstrual_anomaly(previous_start, start, end) is False
