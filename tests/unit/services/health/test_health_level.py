"""``classify_measurement`` 的等級判定（health-alerts spec「等級判定」）。

窮舉恰好等於上下限、超過上下限、同時高於與低於、血糖依量測情境選用上限、
只設定部分範圍、完全未設定六類情境（tasks.md 3.1 的驗證方式）。純函式，
不需要任何 fixture 或替身。
"""

import pytest

from app.models.health import (
    CreateBloodGlucoseRequest,
    CreateBloodPressureRequest,
    HealthAlertThreshold,
)
from app.services.health.health_level import classify_measurement


def _bp(systolic: int, diastolic: int) -> CreateBloodPressureRequest:
    return CreateBloodPressureRequest(systolic=systolic, diastolic=diastolic)


def _glucose(value: int, meal_context: str) -> CreateBloodGlucoseRequest:
    return CreateBloodGlucoseRequest(glucose_mg_dl=value, meal_context=meal_context)


def _thresholds(**kwargs) -> HealthAlertThreshold:
    return HealthAlertThreshold(user_id="U1", updated_by="U1", **kwargs)


# ── 完全未設定範圍 → no_threshold ─────────────────────────────────────


def test_no_thresholds_document_is_no_threshold():
    """沒有提醒範圍文件（thresholds=None），等同全部未設定。"""
    assert classify_measurement(_bp(150, 95), None) == "no_threshold"


def test_blood_pressure_with_no_relevant_bound_set_is_no_threshold():
    thresholds = _thresholds()  # 六項全空
    assert classify_measurement(_bp(200, 120), thresholds) == "no_threshold"


def test_glucose_with_only_the_irrelevant_upper_set_is_no_threshold():
    """空腹讀數只看空腹上限；只設定了餐後上限，與這筆量測無關 → 未設定範圍。"""
    thresholds = _thresholds(glucose_nonfasting_high=180)
    assert classify_measurement(_glucose(150, "fasting"), thresholds) == "no_threshold"


# ── 恰好等於上限或下限 → within_range ─────────────────────────────────


def test_systolic_equal_to_upper_bound_is_within_range():
    thresholds = _thresholds(systolic_high=140)
    assert classify_measurement(_bp(140, 80), thresholds) == "within_range"


def test_glucose_equal_to_lower_bound_is_within_range():
    thresholds = _thresholds(glucose_low=70)
    assert classify_measurement(_glucose(70, "fasting"), thresholds) == "within_range"


# ── 超過上限／下限 ─────────────────────────────────────────────────────


def test_systolic_above_upper_bound_is_above_range():
    thresholds = _thresholds(systolic_high=140)
    assert classify_measurement(_bp(141, 80), thresholds) == "above_range"


def test_glucose_below_lower_bound_is_below_range():
    thresholds = _thresholds(glucose_low=70)
    assert classify_measurement(_glucose(65, "fasting"), thresholds) == "below_range"


# ── 血糖依量測情境選用上限 ─────────────────────────────────────────────


@pytest.mark.parametrize("meal_context", ["fasting", "before_meal"])
def test_glucose_fasting_side_uses_fasting_upper(meal_context):
    thresholds = _thresholds(glucose_fasting_high=130, glucose_nonfasting_high=180)
    assert classify_measurement(_glucose(140, meal_context), thresholds) == "above_range"


@pytest.mark.parametrize("meal_context", ["after_meal", "bedtime", "random"])
def test_glucose_nonfasting_side_uses_nonfasting_upper(meal_context):
    """空腹上限 130、餐後上限 180，餐後血糖 170 落在範圍內
    （health-alerts spec「餐後使用餐後的上限」）。"""
    thresholds = _thresholds(glucose_fasting_high=130, glucose_nonfasting_high=180)
    assert classify_measurement(_glucose(170, meal_context), thresholds) == "within_range"


def test_glucose_low_is_shared_across_meal_contexts():
    thresholds = _thresholds(glucose_low=70)
    assert classify_measurement(_glucose(65, "after_meal"), thresholds) == "below_range"


# ── 同時高於與低於：高於範圍優先 ───────────────────────────────────────


def test_systolic_above_and_diastolic_below_is_above_range():
    """收縮壓上限 140、舒張壓下限 60，收縮壓 150、舒張壓 55
    （health-alerts spec「同時高於與低於」）。"""
    thresholds = _thresholds(systolic_high=140, diastolic_low=60)
    assert classify_measurement(_bp(150, 55), thresholds) == "above_range"


# ── 只設定部分範圍 ─────────────────────────────────────────────────────


def test_partial_range_only_systolic_high_set_within_range_when_not_exceeded():
    """只設定收縮壓上限 140，讀數 120/80 → 範圍內（舒張壓沒有相關範圍，
    不參與比對；收縮壓有相關範圍且未超過）。"""
    thresholds = _thresholds(systolic_high=140)
    assert classify_measurement(_bp(120, 80), thresholds) == "within_range"


def test_partial_range_only_diastolic_low_set_and_exceeded():
    thresholds = _thresholds(diastolic_low=60)
    assert classify_measurement(_bp(120, 55), thresholds) == "below_range"
