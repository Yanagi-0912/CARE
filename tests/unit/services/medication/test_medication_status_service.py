"""查服藥狀況：今天要吃哪些、吃了沒、最近幾天有沒有沒確認的時段。

回覆會原樣送給使用者（不經模型改寫），所以這裡斷言的是使用者看到的文字本身。
假的 repository 照真實 repository 的合約回資料（例如服藥紀錄不含 cancelled、
依時間排序），合約本身另由 repository 的測試釘住。
"""

from datetime import datetime, timedelta

from fastapi import HTTPException

from app.models.family_tree import FamilyMember, FamilyTree
from app.models.medication import (
    TAIPEI_TZ,
    Medication,
    MedicationLog,
    MedicationReminder,
    ensure_aware_utc,
)
from app.services.medication.medication_status_service import MedicationStatusService

NOW = datetime(2026, 9, 14, 12, 40, tzinfo=TAIPEI_TZ)


def _at(day: int, hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 9, day, hour, minute, tzinfo=TAIPEI_TZ)


def _med(med_id: str, name: str, **extra) -> Medication:
    return Medication(
        **{
            "_id": med_id,
            "user_id": "U_ELDER",
            "created_by_user_id": "U_CHILD",
            "name": name,
            "start_date": "2026-09-01",
            **extra,
        }
    )


def _reminder(reminder_id: str, slot: str, entries, **extra) -> MedicationReminder:
    return MedicationReminder(
        **{
            "_id": reminder_id,
            "creator_user_id": "U_CHILD",
            "user_id": "U_ELDER",
            "slot_type": slot,
            "start_date": "2026-09-01",
            "entries": [
                {"meal_timing": timing, "scheduled_time": time, "medication_ids": ids}
                for timing, time, ids in entries
            ],
            **extra,
        }
    )


def _log(reminder, day, status, taken_at=None, taken=()) -> MedicationLog:
    scheduled = _at(day, reminder.scheduled_time)
    return MedicationLog(
        **{
            "_id": f"{reminder.id}-{day}",
            "reminder_id": reminder.id,
            "user_id": "U_ELDER",
            "alert_notify_user_id": "U_CHILD",
            "slot_type": reminder.slot_type,
            "scheduled_at": scheduled,
            "timeout_at": scheduled + timedelta(minutes=30),
            "status": status,
            "taken_at": taken_at,
            "taken_medication_ids": list(taken),
        }
    )


class FakeTrees:
    def __init__(self, trees=None, error=None):
        self._trees = trees or {}
        self._error = error

    async def get_by_user_id(self, user_id):
        if self._error:
            raise self._error
        return self._trees.get(user_id)


class FakeAuthz:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = []

    async def authorize(
        self,
        operator_id,
        target_owner_id,
        classification,
        action,
        has_legacy_equivalent=True,
        now=None,
    ):
        self.calls.append(
            (operator_id, target_owner_id, classification, action, has_legacy_equivalent)
        )
        if not self.allowed:
            raise HTTPException(status_code=403, detail="forbidden")
        return "MEMBER"


class FakeReminders:
    def __init__(self, reminders):
        self._reminders = list(reminders)

    async def list_reminders_by_user(self, user_id):
        return [r for r in self._reminders if r.user_id == user_id]


def _active_on(med: Medication, date_str: str) -> bool:
    return (
        med.enabled
        and med.start_date <= date_str
        and (med.end_date is None or med.end_date >= date_str)
    )


class FakeMedications:
    def __init__(self, meds):
        self._meds = {m.id: m for m in meds}

    async def find_active_by_ids(self, ids, date_str):
        return [self._meds[i] for i in ids if i in self._meds and _active_on(self._meds[i], date_str)]

    async def find_by_ids(self, ids):
        return [self._meds[i] for i in ids if i in self._meds]


class FakeLogs:
    """照 `MedicationLogRepository.list_logs_by_user_between` 的合約：不含 cancelled、依時間排序。"""

    def __init__(self, logs, error=None):
        self._logs = list(logs)
        self._error = error

    async def list_logs_by_user_between(self, user_id, start, end):
        if self._error:
            raise self._error
        hits = [
            log
            for log in self._logs
            if log.user_id == user_id
            and log.status != "cancelled"
            and start <= ensure_aware_utc(log.scheduled_at) < end
        ]
        return sorted(hits, key=lambda log: ensure_aware_utc(log.scheduled_at))


M1 = _med("m1", "脈優錠")
M2 = _med("m2", "普拿疼")
M_ENDED = _med("m3", "史蒂諾斯", end_date="2026-09-10")

MORNING = _reminder(
    "r_morning", "morning", [("before_meal", "07:30", ["m1"]), ("after_meal", "08:30", ["m2"])]
)
NOON = _reminder("r_noon", "noon", [("none", "12:30", ["m2"])])
EVENING = _reminder("r_evening", "evening", [("after_meal", "18:00", ["m1"])])

TODAY_LOGS = [
    _log(MORNING, 14, "taken", taken_at=_at(14, "08:35"), taken=["m1", "m2"]),
    _log(NOON, 14, "pending"),
]
YESTERDAY_LOGS = [
    _log(MORNING, 13, "taken", taken_at=_at(13, "08:02"), taken=["m1", "m2"]),
    _log(NOON, 13, "missed"),
    _log(EVENING, 13, "taken", taken_at=_at(13, "18:10"), taken=["m1"]),
]
# 9/12 服務停機，一筆紀錄都沒有。
EARLIER_LOGS = [
    _log(MORNING, 11, "taken", taken_at=_at(11, "07:40"), taken=["m1", "m2"]),
    _log(NOON, 11, "taken", taken_at=_at(11, "12:31"), taken=["m2"]),
    _log(EVENING, 11, "taken", taken_at=_at(11, "18:05"), taken=["m1"]),
]

MOM = FamilyMember(user_id="U_ELDER", display_name="王美玲", relationship_type="parent")
DAD = FamilyMember(user_id="U_DAD", display_name="王大明", relationship_type="parent")


def _tree(owner, members) -> FamilyTree:
    return FamilyTree(user_id=owner, family_members=members, created_at=NOW, updated_at=NOW)


def _service(
    *,
    trees=None,
    allowed=True,
    reminders=(MORNING, NOON, EVENING),
    meds=(M1, M2),
    logs=(*TODAY_LOGS, *YESTERDAY_LOGS, *EARLIER_LOGS),
    logs_error=None,
    trees_error=None,
):
    authz = FakeAuthz(allowed)
    service = MedicationStatusService(
        family_tree_repository=FakeTrees(trees, error=trees_error),
        authorization_service=authz,
        reminder_repository=FakeReminders(reminders),
        medication_repository=FakeMedications(meds),
        log_repository=FakeLogs(logs, error=logs_error),
    )
    return service, authz


async def _ask(service, asker="U_ELDER", **kwargs):
    return await service.describe(asker, now=NOW, language="zh-TW", **kwargs)


# ── 今天 ────────────────────────────────────────────────────────────


async def test_today_lists_each_slot_with_its_state_and_medicines():
    service, _ = _service()
    assert await _ask(service) == (
        "您今天（9/14）的用藥\n"
        "\n"
        "早 07:30　已確認服用（08:35）\n"
        "✓ 脈優錠（飯前 07:30）\n"
        "✓ 普拿疼（飯後 08:30）\n"
        "\n"
        "中 12:30　還沒確認\n"
        "• 普拿疼\n"
        "\n"
        "晚 18:00　還沒到\n"
        "• 脈優錠（飯後 18:00）"
    )


async def test_partly_confirmed_slot_marks_only_the_confirmed_medicines():
    """逐顆確認到一半時，家人要看得出還差哪一顆。"""
    service, _ = _service(logs=[_log(MORNING, 14, "pending", taken=["m1"])])
    text = await _ask(service)
    assert "早 07:30　還沒確認\n✓ 脈優錠（飯前 07:30）\n• 普拿疼（飯後 08:30）" in text


async def test_missed_slot_today_says_it_timed_out_without_confirmation():
    """系統只知道有沒有按按鈕，所以講「逾時未確認」，不講「漏吃」。"""
    service, _ = _service(logs=[_log(MORNING, 14, "missed")])
    text = await _ask(service)
    assert "早 07:30　逾時未確認" in text
    assert "漏吃" not in text


async def test_upcoming_slot_without_active_medicine_is_not_listed():
    """療程結束的藥不再推播，這裡也不該列——兩邊的判斷要一致。"""
    bedtime = _reminder("r_bed", "bedtime", [("none", "21:30", ["m3"])])
    service, _ = _service(
        reminders=(MORNING, NOON, EVENING, bedtime), meds=(M1, M2, M_ENDED)
    )
    text = await _ask(service)
    assert "睡前" not in text
    assert "史蒂諾斯" not in text


async def test_disabled_reminder_is_not_listed_as_upcoming():
    evening_off = _reminder(
        "r_evening", "evening", [("after_meal", "18:00", ["m1"])], enabled=False
    )
    service, _ = _service(reminders=(MORNING, NOON, evening_off))
    assert "晚 18:00" not in await _ask(service)


async def test_passed_slot_without_a_log_is_not_invented():
    """排程器不為「提醒建立之前」的時段補建紀錄；沒有紀錄就不能自己編一個狀態。"""
    service, _ = _service(reminders=(NOON,), logs=())
    text = await _ask(service)
    assert "普拿疼" not in text
    assert "這天沒有要吃的藥。" in text


# ── 過去某一天 ──────────────────────────────────────────────────────


async def test_yesterday_lists_confirmed_names_and_leaves_unconfirmed_slots_bare():
    """
    沒確認的時段不列藥名：那天該吃哪些只能用現在的規則回推，規則之後改過就會
    列錯。已確認的藥名是按下當下存的，是事實。
    """
    service, _ = _service()
    assert await _ask(service, days_ago=1) == (
        "您昨天（9/13）的用藥\n"
        "\n"
        "早 07:30　已確認服用（08:02）\n"
        "✓ 脈優錠\n"
        "✓ 普拿疼\n"
        "\n"
        "中 12:30　逾時未確認\n"
        "\n"
        "晚 18:00　已確認服用（18:10）\n"
        "✓ 脈優錠"
    )


async def test_pending_log_on_a_past_day_counts_as_unconfirmed():
    service, _ = _service(logs=[_log(NOON, 13, "pending")])
    assert "中 12:30　逾時未確認" in await _ask(service, days_ago=1)


async def test_day_without_any_log_says_there_is_no_record():
    """服務停機的日子一筆紀錄都沒有，不能講成沒吃。"""
    service, _ = _service()
    assert await _ask(service, days_ago=2) == "您 9/12 的用藥\n\n這天沒有紀錄。"


async def test_taken_log_without_a_confirmation_time_still_reads_as_taken():
    """早期寫入的紀錄可能沒有 taken_at。"""
    service, _ = _service(logs=[_log(EVENING, 13, "taken", taken=["m1"])])
    assert "晚 18:00　已確認服用\n✓ 脈優錠" in await _ask(service, days_ago=1)


# ── 最近幾天 ────────────────────────────────────────────────────────


async def test_recent_days_summarise_each_day_newest_first():
    """
    今天還在確認時限內的時段另外標「還沒確認」，不跟逾時未確認混在一起——那不是
    結果，只是還沒到期；但也不能藏起來，家人會以為今天都處理完了。
    """
    service, _ = _service()
    assert await _ask(service, last_n_days=4) == (
        "您最近 4 天的服藥紀錄\n"
        "\n"
        "9/14：已確認 1/2，還沒確認：中 12:30\n"
        "9/13：已確認 2/3，逾時未確認：中 12:30\n"
        "9/12：沒有紀錄\n"
        "9/11：已確認 3/3"
    )


async def test_more_than_seven_days_is_clamped_with_a_notice():
    """範圍是今天加前 6 天：B 選項裡最長的說法是「這禮拜」，再往前就是沒選的任意日期。"""
    service, _ = _service()
    text = await _ask(service, last_n_days=30)
    assert text.startswith(
        "目前只能查最近 7 天，以下是最近 7 天的紀錄。\n\n您最近 7 天的服藥紀錄\n"
    )
    assert text.count("：已確認") + text.count("：沒有紀錄") == 7


async def test_a_single_day_beyond_the_range_only_explains_the_limit():
    service, _ = _service()
    assert await _ask(service, days_ago=10) == "目前只能查最近 7 天的紀錄。"


# ── 查家人 ──────────────────────────────────────────────────────────


async def test_family_member_is_checked_as_a_general_read_on_the_strict_path():
    """
    新路徑一律照 RBAC 判定（has_legacy_equivalent=False），不受影子模式放寬；
    「吃了沒」跟藥名、時段同屬 GENERAL，家族名單裡的人都讀得到。
    """
    service, authz = _service(trees={"U_CHILD": _tree("U_CHILD", [MOM])})
    text = await _ask(service, asker="U_CHILD", person="媽媽", relationship="parent")
    assert text.startswith("王美玲今天（9/14）的用藥\n")
    assert authz.calls == [("U_CHILD", "U_ELDER", "GENERAL", "READ", False)]


async def test_refused_authorization_reveals_no_medication_data():
    service, _ = _service(trees={"U_CHILD": _tree("U_CHILD", [MOM])}, allowed=False)
    text = await _ask(service, asker="U_CHILD", person="美玲")
    assert text == "您沒有查看王美玲用藥的權限。"


async def test_asking_about_oneself_skips_family_authorization():
    service, authz = _service()
    await _ask(service)
    assert authz.calls == []


async def test_ambiguous_person_is_asked_back_with_names():
    service, _ = _service(trees={"U_CHILD": _tree("U_CHILD", [MOM, DAD])})
    text = await _ask(service, asker="U_CHILD", person="媽媽", relationship="parent")
    assert text == "您的家人裡有好幾位符合：王美玲、王大明。請問是哪一位？"


async def test_conflicting_name_and_relationship_are_asked_back():
    service, authz = _service(trees={"U_CHILD": _tree("U_CHILD", [MOM])})
    text = await _ask(service, asker="U_CHILD", person="美玲", relationship="child")
    assert text == "「美玲」與您指定的親屬關係不一致。請確認姓名或關係後再問一次。"
    assert authz.calls == []


async def test_unknown_person_lists_who_is_in_the_family():
    service, _ = _service(trees={"U_CHILD": _tree("U_CHILD", [MOM, DAD])})
    text = await _ask(service, asker="U_CHILD", person="阿嬤", relationship="grandparent")
    assert text == "在您的家人名單裡找不到「阿嬤」。名單上有：王美玲、王大明。可以直接說名字。"


async def test_member_without_a_name_is_still_listed_recognisably():
    nameless = FamilyMember(user_id="U_N", relationship_type="parent")
    service, _ = _service(trees={"U_CHILD": _tree("U_CHILD", [MOM, nameless])})
    text = await _ask(service, asker="U_CHILD", person="媽媽", relationship="parent")
    assert "未設定名字的家人" in text


async def test_asker_without_family_can_only_ask_about_themselves():
    service, _ = _service(trees={})
    text = await _ask(service, asker="U_CHILD", person="媽媽", relationship="parent")
    assert text == "您的家人名單裡還沒有其他人，目前只能查您自己的用藥。"


# ── 沒有提醒、出錯 ──────────────────────────────────────────────────


async def test_no_reminders_for_oneself_suggests_asking_about_family():
    service, _ = _service(reminders=())
    assert await _ask(service) == (
        "您目前沒有設定用藥提醒。要查家人的話，可以說他的名字或關係，"
        "例如「媽媽今天吃藥了嗎」。"
    )


async def test_no_reminders_for_a_family_member_names_them():
    service, _ = _service(trees={"U_CHILD": _tree("U_CHILD", [MOM])}, reminders=())
    assert await _ask(service, asker="U_CHILD", person="美玲") == "王美玲目前沒有設定用藥提醒。"


async def test_database_failure_returns_the_fixed_error_message():
    """不交給模型編：查不到就明說查不到。"""
    service, _ = _service(logs_error=RuntimeError("mongo down"))
    assert await _ask(service) == "暫時查不到用藥紀錄，請稍後再試。"


async def test_family_lookup_failure_returns_the_fixed_error_message():
    service, _ = _service(trees_error=RuntimeError("mongo down"))
    text = await _ask(service, asker="U_CHILD", person="媽媽", relationship="parent")
    assert text == "暫時查不到用藥紀錄，請稍後再試。"


# ── 呈現 ────────────────────────────────────────────────────────────


async def test_reply_is_plain_text_without_markdown():
    """直通回覆不經模型，prompt 規則 2 的禁用 Markdown 要由這裡自己守住。"""
    service, _ = _service()
    for kwargs in ({}, {"days_ago": 1}, {"last_n_days": 7}):
        text = await _ask(service, **kwargs)
        assert "**" not in text and "`" not in text
        assert not any(
            line.lstrip().startswith(("#", "- ", "* ")) for line in text.splitlines()
        )


async def test_reply_follows_the_asker_language():
    service, _ = _service()
    text = await service.describe("U_ELDER", now=NOW, language="en")
    assert "已確認" not in text and "今天" not in text
    assert "9/14" in text


async def test_dates_are_day_first_where_that_is_the_local_convention():
    service, _ = _service()
    text = await service.describe("U_ELDER", now=NOW, language="vi")
    assert "14/9" in text
