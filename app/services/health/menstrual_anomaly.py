"""純函式：依 health-alerts spec「經期異常只通知本人」判定一筆經期紀錄是否
異常。

刻意不做任何 I/O、不取用時鐘——同 ``health_level.classify_measurement`` 的
設計理由：「前一筆的開始日期是哪一筆」「這筆的開始／結束日期是什麼」是呼叫端
（``menstrual_service``、以及 Task 7 的推播）的責任，這裡只負責純粹的門檻
判定，固定住呼叫當下傳入的快照即可測試，不必模擬時間或資料庫狀態。

門檻依 FIGO 2018 異常子宮出血系統的正常範圍（health-alerts spec 「經期異常
只通知本人」）：週期 24–38 天、經期 ≤ 8 天，超出即為異常。

回傳值只回答「異常或不異常」，不回傳是哪一種異常——Task 7 的推播文字
SHALL NOT 包含類別字眼或任何數值（同一份 spec），呼叫端不需要、也不該
知道是週期還是經期天數觸發的。
"""

from datetime import date
from typing import Optional

MIN_NORMAL_CYCLE_DAYS = 24
MAX_NORMAL_CYCLE_DAYS = 38
MAX_NORMAL_PERIOD_DAYS = 8


def detect_menstrual_anomaly(
    previous_start: Optional[date],
    start: date,
    end: Optional[date],
) -> bool:
    """回傳這筆經期是否異常。

    - ``previous_start`` 不是 ``None`` 時，週期長度 = ``(start -
      previous_start).days``；小於 24 或大於 38 天視為異常。
    - ``end`` 不是 ``None`` 時，經期天數 = ``(end - start).days + 1``
      （含頭尾兩天）；大於 8 天視為異常。
    - 兩者是獨立判定：缺席的一半（沒有前一筆、或仍在進行中未填結束日期）
      不參與判定，SHALL NOT 因此視為異常，也 SHALL NOT 因此視為正常——
      單純是「這一半現在還沒有值可以判斷」。任一半判定為異常，整體即為
      異常。
    """
    if previous_start is not None:
        cycle_length = (start - previous_start).days
        if cycle_length < MIN_NORMAL_CYCLE_DAYS or cycle_length > MAX_NORMAL_CYCLE_DAYS:
            return True

    if end is not None:
        period_length = (end - start).days + 1
        if period_length > MAX_NORMAL_PERIOD_DAYS:
            return True

    return False
