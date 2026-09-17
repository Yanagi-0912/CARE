"""每日排程的執行時刻（`HH:MM`，台北時間）解析。

集中在這裡的理由：`*_TIME` 設定值原本是在迴圈裡才 `split(":")` 解析，設錯
（例如 `"9:00 AM"`、`"25:00"`）時排程器會 start 成功、心跳登記成功、log 印出
started，然後在第一次醒來前的 `_next_run_at` 拋 ValueError 把 task 靜默殺掉——
外觀健康、永遠不推。在建構時就驗，錯的設定會讓 lifespan 直接失敗，部署當下
就看得到。
"""

from __future__ import annotations


def parse_run_time(value: str, *, setting_name: str) -> tuple[int, int]:
    """把 `HH:MM` 解析成 (hour, minute)；格式或範圍錯誤時拋 ValueError。

    `setting_name` 只用在錯誤訊息裡，讓維運從 log 直接知道要改哪個環境變數。
    """
    text = (value or "").strip()
    parts = text.split(":")
    if len(parts) != 2 or not all(part.strip().isdigit() for part in parts):
        raise ValueError(
            f"{setting_name} 必須是 HH:MM（台北時間），目前的值是 {value!r}"
        )
    hour, minute = (int(part) for part in parts)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(
            f"{setting_name} 的時刻超出範圍（00:00–23:59），目前的值是 {value!r}"
        )
    return hour, minute
