"""`find_empty_card_logs` 的判斷測試。

這支腳本會改寫使用者的用藥歷史，而它的核心是兩個容易寫錯的地方：
naive UTC → 台北日期的換算，以及「規則掛了藥但當天全失效」的判定。
"""

from datetime import datetime

from scripts.cleanup_expired_course_logs import (
    _is_active_on,
    _taipei_date_str,
    find_empty_card_logs,
)


class _FakeCursor(list):
    def sort(self, *_args, **_kwargs):
        return self


class _FakeCollection:
    def __init__(self, docs):
        self._docs = docs
        self.updated = None

    def find(self, _query=None):
        return _FakeCursor(self._docs)


class _FakeDB:
    name = "CARE_test"

    def __init__(self, reminders, medications, logs):
        self._cols = {
            "medication_reminders": _FakeCollection(reminders),
            "medications": _FakeCollection(medications),
            "medication_logs": _FakeCollection(logs),
        }

    def __getitem__(self, key):
        return self._cols[key]


def _medication(mid, start, end, enabled=True):
    return {"_id": mid, "start_date": start, "end_date": end, "enabled": enabled}


def _log(log_id, scheduled_at, status="missed"):
    return {
        "_id": log_id,
        "reminder_id": "REM_1",
        "user_id": "U_PATIENT",
        "slot_type": "morning",
        "scheduled_at": scheduled_at,
        "status": status,
    }


def test_taipei_date_str_treats_naive_as_utc():
    """08:00 台北的紀錄以 naive UTC 00:00 讀回，不補時區會被算成前一天。"""
    assert _taipei_date_str(datetime(2026, 9, 1, 0, 0)) == "2026-09-01"
    # 台北 07:00 → UTC 前一天 23:00
    assert _taipei_date_str(datetime(2026, 8, 31, 23, 0)) == "2026-09-01"


def test_is_active_on_boundaries():
    med = _medication("M1", "2026-08-17", "2026-08-21")
    assert _is_active_on(med, "2026-08-17") is True
    assert _is_active_on(med, "2026-08-21") is True   # 結束日當天仍有效
    assert _is_active_on(med, "2026-08-22") is False
    assert _is_active_on(med, "2026-08-16") is False
    # end_date 為 None 是長期用藥，永遠有效
    assert _is_active_on(_medication("M2", "2026-08-17", None), "2030-01-01") is True
    # 停用的藥不算
    assert _is_active_on(_medication("M3", "2026-08-17", None, enabled=False), "2026-08-18") is False


def test_finds_only_logs_after_the_course_ended():
    """療程 08-17～08-21：08-21 的紀錄留著，08-22 之後的才是空卡片。"""
    reminders = [{"_id": "REM_1", "user_id": "U_PATIENT", "medication_ids": ["M1"]}]
    medications = [_medication("M1", "2026-08-17", "2026-08-21")]
    logs = [
        _log("L_IN", datetime(2026, 8, 21, 0, 0)),
        _log("L_OUT", datetime(2026, 8, 22, 0, 0)),
        _log("L_OUT2", datetime(2026, 9, 1, 0, 0)),
    ]

    affected = find_empty_card_logs(
        _FakeDB(reminders, medications, logs), {}, datetime(2026, 8, 1)
    )

    assert [log["_id"] for log in affected] == ["L_OUT", "L_OUT2"]


def test_one_surviving_medication_keeps_the_log():
    """同一個時段只要還有一顆藥當天有效，那筆紀錄就不是空卡片。"""
    reminders = [
        {"_id": "REM_1", "user_id": "U_PATIENT", "medication_ids": ["M1", "M2"]}
    ]
    medications = [
        _medication("M1", "2026-08-17", "2026-08-21"),
        _medication("M2", "2026-08-17", None),
    ]
    logs = [_log("L1", datetime(2026, 9, 1, 0, 0))]

    affected = find_empty_card_logs(
        _FakeDB(reminders, medications, logs), {}, datetime(2026, 8, 1)
    )

    assert affected == []


def test_reminders_without_linked_medications_are_out_of_scope():
    """沒掛藥的舊規則不在清理範圍：它們的版面本來就與過去一致。"""
    reminders = []  # 查詢條件 medication_ids != [] 已把它們濾掉
    logs = [_log("L1", datetime(2026, 9, 1, 0, 0))]

    affected = find_empty_card_logs(_FakeDB(reminders, [], logs), {}, datetime(2026, 8, 1))

    assert affected == []
