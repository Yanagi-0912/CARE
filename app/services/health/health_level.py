"""純函式：依 health-alerts spec「等級判定」，將一筆血壓或血糖量測分類為
``within_range`` / ``above_range`` / ``below_range`` / ``no_threshold``。

刻意不做任何 I/O、不取用時鐘——等級只依賴這筆量測的數值與**呼叫端傳入的**
提醒範圍。「本人記錄當下的提醒範圍是哪一份文件」是呼叫端（Task 4 的
``health_measurement_service``）的責任：它決定好、讀出來，這裡只負責純粹的
分類邏輯，這樣「提醒範圍變更後既有紀錄的等級不變」（spec「等級判定」）才有
辦法測——固定住呼叫當下傳入的 ``thresholds`` 快照即可，不必真的模擬時間
流逝。
"""

from typing import List, Optional, Tuple, Union

from app.models.health import (
    CreateBloodGlucoseRequest,
    CreateBloodPressureRequest,
    HealthAlertThreshold,
    HealthLevel,
    MealContext,
)

Measurement = Union[CreateBloodPressureRequest, CreateBloodGlucoseRequest]

# 血糖依量測情境選用上限的欄位名稱對照（health-alerts spec「等級判定」：
# 空腹與餐前用「空腹與餐前的上限」，其餘用「餐後睡前與隨機的上限」；
# 下限共用，不受情境影響）。
_GLUCOSE_UPPER_FIELD_BY_MEAL_CONTEXT: dict[MealContext, str] = {
    "fasting": "glucose_fasting_high",
    "before_meal": "glucose_fasting_high",
    "after_meal": "glucose_nonfasting_high",
    "bedtime": "glucose_nonfasting_high",
    "random": "glucose_nonfasting_high",
}


def _bound(thresholds: Optional[HealthAlertThreshold], field: str) -> Optional[int]:
    """沒有提醒範圍文件，等同這個欄位未設定（design.md「資料格式」）。"""
    if thresholds is None:
        return None
    return getattr(thresholds, field)


def _relevant_pairs(
    measurement: Measurement, thresholds: Optional[HealthAlertThreshold]
) -> List[Tuple[int, Optional[int], Optional[int]]]:
    """回傳這筆量測「相關」的 ``(數值, 上限, 下限)`` 三元組列表。

    「相關」由量測種類決定，與有沒有設定範圍無關：

    - 血壓：收縮壓、舒張壓各自獨立比對自己的上下限，脈搏永遠不在其中
      （health-alerts spec「等級判定」附近的說明，pulse 不是提醒範圍的
      比對對象）。
    - 血糖：只有一組，上限依 ``meal_context`` 二選一，下限共用。

    上限／下限是否為 ``None``（未設定）留給呼叫端判斷「有沒有相關範圍」，
    這裡只負責決定「該比對哪一組」。
    """
    if isinstance(measurement, CreateBloodPressureRequest):
        return [
            (
                measurement.systolic,
                _bound(thresholds, "systolic_high"),
                _bound(thresholds, "systolic_low"),
            ),
            (
                measurement.diastolic,
                _bound(thresholds, "diastolic_high"),
                _bound(thresholds, "diastolic_low"),
            ),
        ]

    upper_field = _GLUCOSE_UPPER_FIELD_BY_MEAL_CONTEXT[measurement.meal_context]
    return [
        (
            measurement.glucose_mg_dl,
            _bound(thresholds, upper_field),
            _bound(thresholds, "glucose_low"),
        ),
    ]


def classify_measurement(
    measurement: Measurement, thresholds: Optional[HealthAlertThreshold]
) -> HealthLevel:
    """依記錄當下的提醒範圍，判定這筆量測的等級（health-alerts spec
    「等級判定」）。

    - 與這筆量測相關的上下限都未設定 → ``no_threshold``（血糖只設定另一個
      情境用不到的上限，也算未設定——空腹讀數只看空腹上限，餐後上限與它
      無關）。
    - 任一相關上限被超過 → ``above_range``，優先於 ``below_range``（同時
      高於與低於時，判定為高於範圍）。
    - 否則任一相關下限被超過 → ``below_range``。
    - 有相關範圍已設定、且都沒被超過 → ``within_range``（恰好等於上限或
      下限視為範圍內）。
    """
    pairs = _relevant_pairs(measurement, thresholds)

    if not any(upper is not None or lower is not None for _, upper, lower in pairs):
        return "no_threshold"

    if any(upper is not None and value > upper for value, upper, _ in pairs):
        return "above_range"

    if any(lower is not None and value < lower for value, _, lower in pairs):
        return "below_range"

    return "within_range"
