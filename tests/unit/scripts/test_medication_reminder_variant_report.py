"""用藥提醒拉霸報表的計數：每個選項有幾頓已經有結果、其中幾頓準時按了。

算錯的方式都很安靜：把還沒走完的算進去會把成功率拉低；時機被清掉（長輩當天改了
提醒設定）的那一頓若照算，會把「+20 分鐘」的結果記到拉霸原本挑的時機頭上。
"""

from datetime import datetime, timedelta, timezone

from app.models.medication import MedicationLog
from scripts.medication_reminder_variant_report import summarize

TIMEOUT = datetime(2026, 9, 14, 0, 30, tzinfo=timezone.utc)


def _log(tone, nudge_minutes, status, taken_at=None, user_id="U1"):
    return MedicationLog(
        reminder_id="R1",
        user_id=user_id,
        alert_notify_user_id="U_CARE",
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
    # 還沒走完：兩台都不算
    _log("family", 15, "pending"),
]


def test_tone_summary_counts_every_option_in_a_fixed_order():
    assert summarize(HISTORY)["tone"] == [("control", 1, 0), ("family", 1, 1), ("brief", 2, 1)]


def test_nudge_summary_skips_reminders_whose_timing_was_cleared():
    assert summarize(HISTORY)["nudge_minutes"] == [(20, 1, 0), (15, 0, 0), (10, 2, 1)]
