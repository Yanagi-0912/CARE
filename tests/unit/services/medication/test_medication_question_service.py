"""拿使用者自己的藥單回答問題。

情境取自 2026-09-23 線上那一則：幽門桿菌除菌療程（早 08:00／晚 18:00 兩頓），
使用者早上那頓拖到 12:00 才吃，然後問「我 11 點喝了牛奶、12 點吃藥，等等要吃
午餐，這樣可以嗎」。

這裡斷言的是使用者看到的文字本身（回覆原樣送出，不經模型改寫），重點在
「時間那幾句必須是程式算的事實」——那正是 RAG 答不出來的部分。
"""

from datetime import datetime, timedelta

from app.i18n.messages import t
from app.models.family_tree import FamilyMember, FamilyTree
from app.models.medication import TAIPEI_TZ, Medication, MedicationLog, MedicationReminder
from app.services.medication.medication_question_service import MedicationQuestionService
from app.services.rag.fail_messages import RagFailCode, rag_fail

from tests.unit.services.medication.test_medication_status_service import (  # noqa: F401
    FakeAuthz,
    FakeLogs,
    FakeMedications,
    FakeReminders,
    FakeTrees,
)

NOW = datetime(2026, 9, 23, 12, 36, tzinfo=TAIPEI_TZ)

AMOX = Medication(
    _id="m_amox", user_id="U1", created_by_user_id="U1",
    name="Amoxicillin 500mg 永信", generic_name="Amoxicillin", start_date="2026-09-20",
)
KLARITH = Medication(
    _id="m_klar", user_id="U1", created_by_user_id="U1",
    name="KlariTH F.C. 500mg 克羅利", generic_name="Clarithromycin", start_date="2026-09-20",
)
DYNIN = Medication(
    _id="m_dyn", user_id="U1", created_by_user_id="U1",
    name="Dynin 帶寧錠 250mg", generic_name="Metronidazole", start_date="2026-09-20",
)
NEXIUM = Medication(
    _id="m_nex", user_id="U1", created_by_user_id="U1",
    name="Nexium 40mg 耐適恩錠", generic_name="Esomeprazole", start_date="2026-09-20",
)
ALL_MEDS = [AMOX, KLARITH, DYNIN, NEXIUM]
ALL_IDS = [m.id for m in ALL_MEDS]


def _reminder(rid, slot, hhmm):
    return MedicationReminder(
        _id=rid, creator_user_id="U1", user_id="U1", slot_type=slot,
        start_date="2026-09-20",
        entries=[{"meal_timing": "after_meal", "scheduled_time": hhmm, "medication_ids": ALL_IDS}],
    )


MORNING = _reminder("r_morning", "morning", "08:00")
EVENING = _reminder("r_evening", "evening", "18:00")


def _log(reminder, status, taken_at=None):
    scheduled = datetime(
        2026, 9, 23, *map(int, reminder.scheduled_time.split(":")), tzinfo=TAIPEI_TZ
    )
    return MedicationLog(
        _id=f"log-{reminder.id}", reminder_id=reminder.id, user_id="U1",
        alert_notify_user_id="U1", slot_type=reminder.slot_type,
        scheduled_at=scheduled, timeout_at=scheduled + timedelta(minutes=30),
        status=status, taken_at=taken_at,
        taken_medication_ids=ALL_IDS if status == "taken" else [],
    )


class FakeRag:
    def __init__(self, answer="牛奶不影響這幾種藥的吸收。\n\n參考資料來源：\n[1] 衛福部：https://ex/1"):
        self.answer_text = answer
        self.queries = []

    async def answer(self, user_text):
        self.queries.append(user_text)
        return self.answer_text


def _service(*, logs, rag=None, meds=ALL_MEDS, reminders=(MORNING, EVENING), trees=None):
    return MedicationQuestionService(
        family_tree_repository=trees or FakeTrees(),
        authorization_service=FakeAuthz(),
        reminder_repository=FakeReminders(reminders),
        medication_repository=FakeMedications(meds),
        log_repository=FakeLogs(logs),
        rag_answer_service=rag or FakeRag(),
    )


async def _ask(service, question="我 11 點喝了牛奶、12 點吃藥，等等要吃午餐，這樣可以嗎", **kw):
    return await service.answer("U1", question, now=NOW, language="zh-TW", **kw)


# ── 藥理那半段交給 RAG ──────────────────────────────────────────────


async def test_drug_names_are_appended_to_the_rag_query():
    """RAG 看不到使用者在吃什麼——這個工具存在的理由就是把藥名遞過去。"""
    rag = FakeRag()
    await _ask(_service(logs=[_log(MORNING, "pending")], rag=rag))

    (query,) = rag.queries
    assert query.startswith("我 11 點喝了牛奶")          # 問題原話在前，不改寫
    for generic in ("Amoxicillin", "Clarithromycin", "Metronidazole", "Esomeprazole"):
        assert generic in query


async def test_query_prefers_the_generic_name():
    """知識庫與可信網域寫的是成分，不是藥袋上的「耐適恩錠」。"""
    rag = FakeRag()
    await _ask(_service(logs=[], rag=rag))
    assert "Esomeprazole" in rag.queries[0]
    assert "耐適恩錠" not in rag.queries[0]


async def test_rag_answer_comes_first_and_keeps_its_sources():
    text = await _ask(_service(logs=[_log(MORNING, "pending")]))
    assert "牛奶不影響這幾種藥的吸收。" in text
    assert "https://ex/1" in text
    # 登記資料插在來源清單之前，卡片才看得到（同 rag_direct 的理由）。
    assert text.index("CARE 裡登記的資料") < text.index("參考資料來源：")


# ── 時間那半段由程式算 ──────────────────────────────────────────────


async def test_late_dose_and_shortened_gap_are_stated_as_numbers():
    """
    早 08:00 那頓 12:00 才確認：晚了 4 小時，離晚 18:00 只剩 6 小時，而排定的
    間隔是 10 小時。三個數字都是資料庫裡的減法，沒有一篇衛教文章知道。
    """
    logs = [
        _log(MORNING, "taken", taken_at=datetime(2026, 9, 23, 12, 0, tzinfo=TAIPEI_TZ)),
    ]
    text = await _ask(_service(logs=logs))

    assert "早 08:00 那一頓在 12:00 才確認，比排定時間晚 4 小時" in text
    assert "12:00 到下一頓晚 18:00 只隔 6 小時，排定的間隔是 10 小時" in text


async def test_no_timing_note_when_nothing_was_confirmed_today():
    text = await _ask(_service(logs=[_log(MORNING, "pending")]))
    assert "才確認" not in text
    assert "只隔" not in text


async def test_small_delay_is_not_worth_saying():
    """差不到一小時是雜訊：提醒本身的解析度就是整點時段。"""
    logs = [_log(MORNING, "taken", taken_at=datetime(2026, 9, 23, 8, 40, tzinfo=TAIPEI_TZ))]
    text = await _ask(_service(logs=logs))
    assert "比排定時間晚" not in text
    assert "只隔" not in text


async def test_the_last_dose_of_the_day_has_no_next_slot():
    logs = [_log(EVENING, "taken", taken_at=datetime(2026, 9, 23, 22, 0, tzinfo=TAIPEI_TZ))]
    text = await _ask(_service(logs=logs))
    assert "比排定時間晚 4 小時" in text
    assert "只隔" not in text


async def test_timing_notes_never_tell_the_user_what_to_do():
    """隔多久算安全是醫囑。只給數字，並叫他問藥師。"""
    logs = [_log(MORNING, "taken", taken_at=datetime(2026, 9, 23, 12, 0, tzinfo=TAIPEI_TZ))]
    text = await _ask(_service(logs=logs))
    assert "請先問藥師或醫師" in text
    for word in ("太近", "建議延後", "不要吃", "應該延到"):
        assert word not in text


# ── 沒有藥單 / RAG 失敗 ─────────────────────────────────────────────


async def test_no_registered_medication_says_so_instead_of_generic_advice():
    """使用者問的是「我的藥」，沒有藥單時這個工具不成立，不能偷偷改答一般衛教。"""
    rag = FakeRag()
    text = await _ask(_service(logs=[], meds=[], reminders=[], rag=rag))
    assert text == t("medstatus.no_reminders.self", "zh-TW")
    assert rag.queries == []  # 沒有藥單就不該白打一次 RAG


async def test_rag_failure_still_returns_the_facts():
    """時間那段跟 RAG 成不成功無關，而它正是使用者最需要的部分。"""
    rag = FakeRag(rag_fail(RagFailCode.KB_EMPTY, "zh-TW"))
    logs = [_log(MORNING, "taken", taken_at=datetime(2026, 9, 23, 12, 0, tzinfo=TAIPEI_TZ))]
    text = await _ask(_service(logs=logs, rag=rag))

    assert "RAG_ERR" not in text
    assert "請直接問藥師或醫師" in text
    assert "比排定時間晚 4 小時" in text


async def test_a_broken_repository_never_leaks_an_exception():
    """回傳值直接送給使用者；丟例外只會讓模型接手、自己編一個答案。"""
    service = _service(logs=[])
    service._reminders = None  # 下一個呼叫必然爆炸
    assert await _ask(service) == t("medstatus.error", "zh-TW")


# ── 家人 ────────────────────────────────────────────────────────────


async def test_asking_about_a_family_member_goes_through_authorization():
    tree = FamilyTree(
        user_id="U1",
        family_members=[
            FamilyMember(user_id="U1", display_name="我"),
            FamilyMember(user_id="U_MOM", display_name="王美玲", relationship_type="parent"),
        ],
        created_at=NOW,
        updated_at=NOW,
    )
    authz = FakeAuthz(allowed=False)
    service = MedicationQuestionService(
        family_tree_repository=FakeTrees({"U1": tree}),
        authorization_service=authz,
        reminder_repository=FakeReminders([MORNING]),
        medication_repository=FakeMedications(ALL_MEDS),
        log_repository=FakeLogs([]),
        rag_answer_service=FakeRag(),
    )
    text = await service.answer(
        "U1", "媽媽的藥可以配牛奶嗎", person="媽媽", relationship="parent",
        now=NOW, language="zh-TW",
    )
    assert text == t("medstatus.no_permission", "zh-TW").format(name="王美玲")
    assert authz.calls  # 授權確實被問過，而不是「查不到就算了」
