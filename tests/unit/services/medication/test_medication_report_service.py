"""在聊天裡回報吃過藥了。

2026-09-23 線上那一則：使用者在訊息裡明說「我剛剛 12 點吃了藥」，下一秒回的
清單上，早上那一頓還寫著「逾時未確認」——確認只認推播卡片上的按鈕。

回覆原樣送給使用者，所以這裡斷言的是他看到的文字本身。
"""

from datetime import datetime, timedelta

from fastapi import HTTPException

from app.i18n.messages import t
from app.models.medication import TAIPEI_TZ, MedicationLog, MedicationReminder
from app.services.medication.medication_report_service import MedicationReportService

from tests.unit.services.medication.test_medication_status_service import FakeLogs  # noqa: F401

NOW = datetime(2026, 9, 23, 12, 36, tzinfo=TAIPEI_TZ)


def _reminder(rid, slot, hhmm):
    return MedicationReminder(
        _id=rid, creator_user_id="U1", user_id="U1", slot_type=slot,
        start_date="2026-09-20",
        entries=[{"meal_timing": "after_meal", "scheduled_time": hhmm, "medication_ids": ["m1"]}],
    )


MORNING = _reminder("r_morning", "morning", "08:00")
NOON = _reminder("r_noon", "noon", "12:00")
EVENING = _reminder("r_evening", "evening", "18:00")


def _log(reminder, status="pending"):
    scheduled = datetime(
        2026, 9, 23, *map(int, reminder.scheduled_time.split(":")), tzinfo=TAIPEI_TZ
    )
    return MedicationLog(
        _id=f"log-{reminder.id}", reminder_id=reminder.id, user_id="U1",
        alert_notify_user_id="U1", slot_type=reminder.slot_type,
        scheduled_at=scheduled, timeout_at=scheduled + timedelta(minutes=30),
        status=status,
    )


class FakeMedicationService:
    """只記下被確認的是哪一筆、算在哪一刻，其餘照 MedicationService 的合約回值。"""

    def __init__(self, names=("Amoxicillin 500mg 永信",), error=None):
        self.confirmed = []
        self._names = list(names)
        self._error = error

    async def confirm_medication(self, log_id, user_id, medication_id=None, taken_at=None):
        if self._error:
            raise self._error
        self.confirmed.append((log_id, user_id, taken_at))
        scheduled = datetime(2026, 9, 23, 8, 0, tzinfo=TAIPEI_TZ)
        return MedicationLog(
            _id=log_id, reminder_id="r", user_id=user_id, alert_notify_user_id=user_id,
            slot_type="morning", scheduled_at=scheduled,
            timeout_at=scheduled + timedelta(minutes=30),
            status="taken", taken_at=taken_at or NOW, taken_medication_ids=["m1"],
        )

    async def list_medication_names_for_log(self, log):
        return list(self._names)


def _service(logs, medications=None):
    return MedicationReportService(
        medication_service=medications or FakeMedicationService(),
        log_repository=FakeLogs(logs),
    )


async def _report(service, **kw):
    return await service.record_taken("U1", now=NOW, language="zh-TW", **kw)


# ── 挑出是哪一頓 ────────────────────────────────────────────────────


async def test_stated_time_picks_the_dose_that_was_already_due():
    """「我 12 點吃了藥」而早上那頓還沒確認 → 指的是早 08:00 那頓。"""
    meds = FakeMedicationService()
    service = _service([_log(MORNING), _log(EVENING)], meds)

    text = await _report(service, taken_time="12:00")

    (log_id, user_id, taken_at) = meds.confirmed[0]
    assert log_id == "log-r_morning"
    assert user_id == "U1"
    assert taken_at == datetime(2026, 9, 23, 12, 0, tzinfo=TAIPEI_TZ)
    assert "12:00" in text


async def test_stated_time_is_stored_as_taken_at_not_now():
    """
    使用者說 12:00 吃的，那就是 12:00。存成「現在」會讓服藥狀況顯示一個他從未
    服藥的時刻，也會讓拖了四小時的那一頓被拉霸算進別的時間帶。
    """
    meds = FakeMedicationService()
    await _report(_service([_log(MORNING)], meds), taken_time="12:00")
    assert meds.confirmed[0][2] == datetime(2026, 9, 23, 12, 0, tzinfo=TAIPEI_TZ)


async def test_no_stated_time_uses_now():
    meds = FakeMedicationService()
    await _report(_service([_log(MORNING)], meds))
    assert meds.confirmed[0][2] == NOW


async def test_a_future_time_falls_back_to_now():
    """今天還沒到的時刻不可能已經吃過：那是把「等等要吃」聽成「吃了」。"""
    meds = FakeMedicationService()
    await _report(_service([_log(MORNING)], meds), taken_time="18:00")
    assert meds.confirmed[0][2] == NOW


async def test_latest_due_dose_wins_when_several_are_open():
    """更早那些是更久以前就沒確認的，「剛剛吃了」指的不會是它們。"""
    meds = FakeMedicationService()
    service = _service([_log(MORNING), _log(NOON)], meds)
    await _report(service, taken_time="12:30")
    assert meds.confirmed[0][0] == "log-r_noon"


async def test_named_slot_narrows_the_choice():
    meds = FakeMedicationService()
    service = _service([_log(MORNING), _log(NOON)], meds)
    await _report(service, slot="morning")
    assert meds.confirmed[0][0] == "log-r_morning"


async def test_asks_back_when_several_future_doses_are_open():
    """猜錯會把另一頓標成吃過，之後就不再催促、也不會通知家人。"""
    meds = FakeMedicationService()
    early = datetime(2026, 9, 23, 6, 0, tzinfo=TAIPEI_TZ)
    service = MedicationReportService(
        medication_service=meds, log_repository=FakeLogs([_log(MORNING), _log(EVENING)])
    )
    text = await service.record_taken("U1", now=early, language="zh-TW")

    assert meds.confirmed == []
    assert "早 08:00" in text and "晚 18:00" in text


async def test_single_future_dose_is_taken_as_the_one_they_mean():
    """只有一個候選就沒有猜的餘地——他提早吃了。"""
    meds = FakeMedicationService()
    early = datetime(2026, 9, 23, 6, 0, tzinfo=TAIPEI_TZ)
    service = MedicationReportService(
        medication_service=meds, log_repository=FakeLogs([_log(MORNING)])
    )
    await service.record_taken("U1", now=early, language="zh-TW")
    assert meds.confirmed[0][0] == "log-r_morning"


# ── 什麼都不做的情況 ────────────────────────────────────────────────


async def test_nothing_open_today():
    meds = FakeMedicationService()
    assert await _report(_service([_log(MORNING, "taken")], meds)) == t(
        "medreport.nothing_open", "zh-TW"
    )
    assert meds.confirmed == []


async def test_named_slot_with_nothing_open_does_not_touch_another_slot():
    """使用者指名了哪一頓；找不到就照實說，不能改動別的時段。"""
    meds = FakeMedicationService()
    text = await _report(_service([_log(NOON)], meds), slot="morning")
    assert meds.confirmed == []
    assert text == t("medreport.slot_not_open", "zh-TW").format(slot=t("slot.morning", "zh-TW"))


async def test_only_the_person_themselves_can_confirm():
    """與按鈕同一條界線：家人替他按下「吃過了」是替另一個人的病歷作證。"""
    meds = FakeMedicationService()
    text = await _report(_service([_log(MORNING)], meds), person="媽媽", relationship="parent")
    assert meds.confirmed == []
    assert text == t("medreport.self_only", "zh-TW")


async def test_a_rejected_confirmation_tells_the_user_it_did_not_stick():
    meds = FakeMedicationService(error=HTTPException(status_code=403, detail="no"))
    text = await _report(_service([_log(MORNING)], meds))
    assert text == t("medreport.error", "zh-TW")


async def test_an_unexpected_failure_never_leaks_an_exception():
    service = _service([_log(MORNING)])
    service._logs = None
    assert await _report(service) == t("medreport.error", "zh-TW")


# ── 回覆內容 ────────────────────────────────────────────────────────


async def test_reply_names_the_slot_and_the_medicines():
    """使用者要拿這則回覆核對「有沒有記到對的那一頓」。"""
    meds = FakeMedicationService(names=("Amoxicillin 500mg 永信", "Nexium 40mg 耐適恩錠"))
    text = await _report(_service([_log(MORNING)], meds), taken_time="12:00")

    assert "早 08:00" in text
    assert "✓ Amoxicillin 500mg 永信" in text
    assert "✓ Nexium 40mg 耐適恩錠" in text


# ── 回的是卡片 ──────────────────────────────────────────────────────


def _payload(text):
    """服務回的是 Flex payload 的 JSON（見 reply.py `_try_parse_flex_message`）。"""
    import json

    assert text.startswith("{") and text.endswith("}")
    return json.loads(text)


def _actions(node):
    found = []
    if isinstance(node, dict):
        if isinstance(node.get("action"), dict):
            found.append(node["action"])
        for value in node.values():
            found.extend(_actions(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_actions(item))
    return found


async def test_success_reply_is_a_card_with_an_undo_button():
    meds = FakeMedicationService()
    text = await _report(_service([_log(MORNING)], meds), taken_time="12:00")

    payload = _payload(text)
    assert payload["type"] == "flex"
    (action,) = [a for a in _actions(payload["contents"]) if a.get("type") == "postback"]
    assert action["data"] == "action=undo_medication_report&log_id=log-r_morning"


async def test_success_card_carries_the_plain_text_for_tts():
    """朗讀的內容要與卡片一致；卡片路徑沒有別的地方拿得到文字。"""
    meds = FakeMedicationService()
    payload = _payload(await _report(_service([_log(MORNING)], meds), taken_time="12:00"))
    speech = payload["speechText"]
    assert "早 08:00" in speech and "12:00" in speech
    assert "✓ Amoxicillin 500mg 永信" in speech


async def test_which_slot_reply_is_a_card_with_one_button_per_candidate():
    """使用者不必再打一次字，系統也不必再判讀一次「他說的是哪一頓」。"""
    early = datetime(2026, 9, 23, 6, 0, tzinfo=TAIPEI_TZ)
    service = MedicationReportService(
        medication_service=FakeMedicationService(),
        log_repository=FakeLogs([_log(MORNING), _log(EVENING)]),
    )
    payload = _payload(await service.record_taken("U1", now=early, language="zh-TW"))

    actions = [a for a in _actions(payload["contents"]) if a.get("type") == "postback"]
    assert [a["label"] for a in actions] == ["早 08:00", "晚 18:00"]
    assert actions[0]["data"].endswith("&at=06:00")


async def test_plain_text_replies_stay_plain_text():
    """沒有按鈕可按的幾則做成卡片只是噪音。"""
    meds = FakeMedicationService()
    assert not (await _report(_service([_log(MORNING, "taken")], meds))).startswith("{")
    assert not (
        await _report(_service([_log(MORNING)], meds), person="媽媽", relationship="parent")
    ).startswith("{")


async def test_falls_back_to_plain_text_when_the_card_is_too_big():
    """卡片超過 LINE 上限會被 400 拒收，使用者什麼都收不到。"""
    meds = FakeMedicationService(names=tuple(f"藥名{i}" * 40 for i in range(200)))
    text = await _report(_service([_log(MORNING)], meds), taken_time="12:00")
    assert not text.startswith("{")
    assert "早 08:00" in text


# ── 記錯了 ──────────────────────────────────────────────────────────


async def test_undo_reverts_the_confirmation_and_returns_a_cancelled_card():
    class Reverting(FakeMedicationService):
        def __init__(self):
            super().__init__()
            self.reverted = []

        async def revert_confirmation(self, log_id, user_id):
            self.reverted.append((log_id, user_id))
            scheduled = datetime(2026, 9, 23, 8, 0, tzinfo=TAIPEI_TZ)
            return MedicationLog(
                _id=log_id, reminder_id="r", user_id=user_id,
                alert_notify_user_id=user_id, slot_type="morning",
                scheduled_at=scheduled, timeout_at=scheduled + timedelta(minutes=30),
                status="pending",
            )

    meds = Reverting()
    service = MedicationReportService(medication_service=meds, log_repository=FakeLogs([]))
    bubble, text = await service.undo("U1", "log-r_morning", language="zh-TW")

    assert meds.reverted == [("log-r_morning", "U1")]
    assert bubble is not None
    assert [a for a in _actions(bubble) if a.get("type") == "postback"] == []
    assert "早 08:00" in text


async def test_pressing_undo_twice_says_so_instead_of_erroring():
    """第二次按下去看到錯誤訊息，使用者會以為第一次也沒生效。"""
    class NothingToRevert(FakeMedicationService):
        async def revert_confirmation(self, log_id, user_id):
            return None

    service = MedicationReportService(
        medication_service=NothingToRevert(), log_repository=FakeLogs([])
    )
    bubble, text = await service.undo("U1", "log-x", language="zh-TW")
    assert bubble is None
    assert text == t("medreport.undo_failed", "zh-TW")


async def test_undo_never_leaks_an_exception():
    class Broken(FakeMedicationService):
        async def revert_confirmation(self, log_id, user_id):
            raise RuntimeError("db down")

    service = MedicationReportService(
        medication_service=Broken(), log_repository=FakeLogs([])
    )
    bubble, text = await service.undo("U1", "log-x", language="zh-TW")
    assert bubble is None
    assert text == t("medreport.undo_failed", "zh-TW")
