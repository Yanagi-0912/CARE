"""用藥提醒拉霸報表的計數：每個選項有幾頓已經有結果、其中幾頓準時按了。

算錯的方式都很安靜：
- 把還沒走完的算進去，會把準時率拉低。
- 時機被清掉（長輩當天改了提醒設定）的那一頓若照算，會把「+20 分鐘」的結果記到
  拉霸原本挑的時機頭上。
- 語氣不分「家屬設的／長輩自己設的」提醒：家人版只出現在前者，兩群原本的準時率
  不同時，差距會被誤讀成語氣的效果。
"""

from datetime import datetime, timedelta, timezone

from app.models.medication import MedicationLog
from scripts.medication_reminder_variant_report import summarize

TIMEOUT = datetime(2026, 9, 14, 0, 30, tzinfo=timezone.utc)


def _log(tone, nudge_minutes, status, taken_at=None, alert_notify_user_id="U_CARE"):
    return MedicationLog(
        reminder_id="R1",
        user_id="U1",
        alert_notify_user_id=alert_notify_user_id,
        slot_type="morning",
        scheduled_at=TIMEOUT - timedelta(minutes=30),
        timeout_at=TIMEOUT,
        status=status,
        taken_at=taken_at,
        reminder_tone=tone,
        nudge_minutes=nudge_minutes,
    )


HISTORY = [
    _log("brief", 10, "taken", TIMEOUT - timedelta(minutes=3)),
    _log("brief", 10, "missed"),
    # 家屬被通知之後才按：算失敗
    _log("control", 20, "taken", TIMEOUT + timedelta(minutes=4)),
    # 當天改了提醒設定，時機被清掉：語氣照算，時機不算
    _log("family", None, "taken", TIMEOUT - timedelta(minutes=10)),
    # 長輩自己設的提醒
    _log("brief", 15, "taken", TIMEOUT - timedelta(minutes=1), alert_notify_user_id="U1"),
    # 還沒走完：都不算
    _log("family", 15, "pending"),
]


def test_tone_rows_are_split_by_who_set_the_reminder():
    summary = summarize(HISTORY)
    assert summary["tone_family_set"] == [("control", 1, 0), ("family", 1, 1), ("brief", 2, 1)]
    assert summary["tone_self_set"] == [("control", 0, 0), ("brief", 1, 1)]


def test_nudge_rows_skip_reminders_whose_timing_was_cleared():
    assert summarize(HISTORY)["nudge_minutes"] == [(20, 1, 0), (15, 1, 1), (10, 2, 1)]
