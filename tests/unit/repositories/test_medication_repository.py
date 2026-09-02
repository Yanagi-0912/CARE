from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock
import pytest
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.models.medication import MedicationLog, MedicationReminder
from app.repositories.medication_repository import (
    MedicationLogRepository,
    MedicationReminderRepository,
    MedicationRepository,
    _active_date_window,
)


@pytest.fixture()
def override_medication_reminders_col(monkeypatch):
    def _override(col):
        monkeypatch.setattr(
            "app.repositories.medication_repository.MongoDBManager.get_medication_reminders_collection",
            lambda: col,
        )
        return col

    return _override


@pytest.fixture()
def override_medication_logs_col(monkeypatch):
    def _override(col):
        monkeypatch.setattr(
            "app.repositories.medication_repository.MongoDBManager.get_medication_logs_collection",
            lambda: col,
        )
        return col

    return _override


@pytest.mark.asyncio
async def test_create_reminder(override_medication_reminders_col):
    col = MagicMock()
    col.insert_one = AsyncMock(return_value=MagicMock(inserted_id="fake_id"))
    override_medication_reminders_col(col)

    reminder = MedicationReminder(
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        scheduled_time="08:00",
        start_date="2026-07-25",
    )
    result = await MedicationReminderRepository.create_reminder(reminder)

    assert result.creator_user_id == "U_CARE"
    assert result.user_id == "U_PATIENT"
    assert result.start_date == "2026-07-25"
    col.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_find_or_create_reminder_upserts_atomically():
    """
    藥袋提交流程靠這個方法避免「一個時段變成兩份規則」的競態。這裡驗證的是
    查詢條件（只看 user_id/slot_type，不篩 enabled 或日期）、upsert 旗標與
    只在建立時套用的欄位——單一呼叫的原子性由 MongoDB 保證，不是這個測試
    能驗證的範圍；跨呼叫的重複插入風險則見方法本身的說明。
    """
    from app.repositories.medication_repository import MedicationReminderRepository

    col = MagicMock()
    col.find_one_and_update = AsyncMock(
        return_value={
            "_id": "R_NEW",
            "creator_user_id": "U_FAMILY",
            "user_id": "U_PATIENT",
            "slot_type": "morning",
            "scheduled_time": "08:00",
            "start_date": "2026-08-10",
            "enabled": True,
            "medication_ids": [],
        }
    )

    reminder, reactivated = await MedicationReminderRepository.find_or_create_reminder(
        user_id="U_PATIENT",
        slot_type="morning",
        creator_user_id="U_FAMILY",
        scheduled_time="08:00",
        collection=col,
    )

    assert reminder.id == "R_NEW"
    assert reactivated is False
    # 新插入的文件本身就可排程（enabled=True、start_date 是今天），不需要
    # 第二次寫入去「修好」它——只呼叫了一次 find_one_and_update。
    col.find_one_and_update.assert_awaited_once()
    (query, update), kwargs = col.find_one_and_update.call_args
    assert query == {"user_id": "U_PATIENT", "slot_type": "morning"}
    set_on_insert = update["$setOnInsert"]
    assert set_on_insert["creator_user_id"] == "U_FAMILY"
    assert set_on_insert["scheduled_time"] == "08:00"
    assert set_on_insert["enabled"] is True
    assert set_on_insert["medication_ids"] == []
    assert kwargs.get("upsert") is True
    assert kwargs.get("return_document") is ReturnDocument.AFTER


@pytest.mark.asyncio
async def test_find_or_create_reminder_returns_existing_live_reminder_without_overwriting():
    """命中一筆「活著」（啟用中且日期仍有效）的既有規則時只回傳它，不執行
    任何第二次寫入——不能因為又有人提交同一個時段的藥，就悄悄覆蓋使用者
    已經調整過的 scheduled_time 等設定。
    """
    from app.repositories.medication_repository import MedicationReminderRepository

    col = MagicMock()
    col.find_one_and_update = AsyncMock(
        return_value={
            "_id": "R_EXISTING",
            "creator_user_id": "U_OTHER_CREATOR",
            "user_id": "U_PATIENT",
            "slot_type": "morning",
            "scheduled_time": "09:15",
            "enabled": True,
        }
    )

    reminder, reactivated = await MedicationReminderRepository.find_or_create_reminder(
        user_id="U_PATIENT",
        slot_type="morning",
        creator_user_id="U_FAMILY",
        scheduled_time="08:00",
        collection=col,
    )

    assert reminder.id == "R_EXISTING"
    assert reminder.creator_user_id == "U_OTHER_CREATOR"
    assert reminder.scheduled_time == "09:15"
    assert reminder.enabled is True
    assert reactivated is False
    # 只呼叫了一次：既有規則本身可排程，完全沒有第二次寫入去動它。
    col.find_one_and_update.assert_awaited_once()


class _FakeReminderCollection:
    """支援 find_or_create_reminder 所用查詢語法（等值、$and、$or、
    $exists、$lte、$gte、$set）的極簡 in-memory 假集合。

    這裡要驗證的是「查詢條件本身會不會命中一筆規則、命中後 $set 有沒有
    真的把它改回可排程狀態」——純 MagicMock 只能回報呼叫時傳了什麼查詢
    字典，驗證不了查詢字典對一份既有文件到底 match 不 match，也不會真的
    套用 $set，所以需要一個會真的做比對與寫入的假集合。
    """

    def __init__(self, existing_doc: Optional[dict] = None):
        self.docs: list[dict] = [dict(existing_doc)] if existing_doc else []

    async def find_one_and_update(self, query, update, upsert=False, return_document=None):
        match = next((doc for doc in self.docs if self._matches(doc, query)), None)
        if match is None:
            if not upsert:
                return None
            new_doc: dict = {
                key: value for key, value in query.items() if not key.startswith("$")
            }
            new_doc.update(update.get("$setOnInsert", {}))
            self.docs.append(new_doc)
            return new_doc
        if "$set" in update:
            match.update(update["$set"])
        return match

    @classmethod
    def _matches(cls, doc: dict, query: dict) -> bool:
        for key, condition in query.items():
            if key == "$and":
                if not all(cls._matches(doc, clause) for clause in condition):
                    return False
            elif key == "$or":
                if not any(cls._matches(doc, clause) for clause in condition):
                    return False
            elif isinstance(condition, dict):
                for op, value in condition.items():
                    if op == "$exists":
                        if (key in doc) != value:
                            return False
                    elif op == "$lte":
                        if key not in doc or doc[key] is None or doc[key] > value:
                            return False
                    elif op == "$gte":
                        if key not in doc or doc[key] is None or doc[key] < value:
                            return False
                    else:
                        raise NotImplementedError(op)
            else:
                if doc.get(key) != condition:
                    return False
        return True


@pytest.mark.asyncio
async def test_find_or_create_reminder_reactivates_a_disabled_reminder():
    """家屬先前手動關掉了這個時段（例如同時段其他藥暫停），今天掃描新藥袋
    命中同一個時段：一個時段永遠只該有一份規則，所以要重用同一筆文件，
    並把它改回可排程狀態，而不是另外插入第二筆——否則兩筆規則都可能
    被排程器同時挑中，使用者會收到兩則同一時段的推播。

    這筆規則底下原本掛著的藥（M_OLD）連帶恢復收到提醒，是刻意的：
    這正是 reactivated 這個回傳值存在的理由——呼叫端據此在使用者確認前
    先揭露、送出後的訊息也要如實告知，而不是靜默發生。
    """
    from app.repositories.medication_repository import MedicationReminderRepository

    col = _FakeReminderCollection(
        existing_doc={
            "_id": "R_DISABLED",
            "creator_user_id": "U_FAMILY",
            "user_id": "U_PATIENT",
            "slot_type": "morning",
            "scheduled_time": "07:30",
            "start_date": "2026-06-01",
            "end_date": None,
            "enabled": False,
            "medication_ids": ["M_OLD"],
        }
    )

    reminder, reactivated = await MedicationReminderRepository.find_or_create_reminder(
        user_id="U_PATIENT",
        slot_type="morning",
        creator_user_id="U_FAMILY",
        scheduled_time="08:00",
        collection=col,
    )

    assert reminder.id == "R_DISABLED"
    assert reminder.enabled is True
    assert reactivated is True
    # 這個時段最終只剩一份文件，且它底下原本的藥品關聯原封不動。
    assert len(col.docs) == 1
    assert reminder.medication_ids == ["M_OLD"]
    # 不是使用者自訂的欄位（scheduled_time）沒有被動到：家屬把這個時段
    # 調成 07:30，重新啟用不能把它悄悄改回這次請求帶的 08:00。
    assert reminder.scheduled_time == "07:30"


@pytest.mark.asyncio
async def test_find_or_create_reminder_reactivates_an_expired_reminder():
    """一份處方療程已經結束（end_date 已過），今天是另一份新處方：
    同樣要重用同一份文件並清空過期的 end_date，而不是插入第二筆——否則
    新藥可能被排程器的日期區間篩選條件擋下、從未真正推播過，同時原本
    那份「已過期」的規則仍在，兩份文件都對應同一個時段。
    """
    from app.repositories.medication_repository import MedicationReminderRepository

    col = _FakeReminderCollection(
        existing_doc={
            "_id": "R_EXPIRED",
            "creator_user_id": "U_FAMILY",
            "user_id": "U_PATIENT",
            "slot_type": "morning",
            "scheduled_time": "07:30",
            "start_date": "2026-06-01",
            "end_date": "2026-07-31",
            "enabled": True,
            "medication_ids": ["M_OLD"],
        }
    )

    reminder, reactivated = await MedicationReminderRepository.find_or_create_reminder(
        user_id="U_PATIENT",
        slot_type="morning",
        creator_user_id="U_FAMILY",
        scheduled_time="08:00",
        collection=col,
    )

    assert reminder.id == "R_EXPIRED"
    assert reminder.enabled is True
    assert reminder.end_date is None
    assert reactivated is True
    assert len(col.docs) == 1
    assert reminder.medication_ids == ["M_OLD"]
    # 不是使用者自訂的欄位（scheduled_time）沒有被動到。
    assert reminder.scheduled_time == "07:30"


@pytest.mark.asyncio
async def test_find_or_create_reminder_pulls_back_a_future_start_date():
    """這筆規則的 start_date 訂在未來（LIFF 的日期輸入框沒有 min 限制，
    使用者可以合法地這樣設定）：掃描當下它還沒到 start_date、不會被排程器
    挑中，一樣要重用同一份文件並把 start_date 拉回今天，而不是插入第二筆。
    """
    from app.repositories.medication_repository import (
        MedicationReminderRepository,
        _today_date_str,
    )

    col = _FakeReminderCollection(
        existing_doc={
            "_id": "R_FUTURE",
            "creator_user_id": "U_FAMILY",
            "user_id": "U_PATIENT",
            "slot_type": "morning",
            "scheduled_time": "07:30",
            "start_date": "2099-01-01",
            "end_date": None,
            "enabled": True,
            "medication_ids": ["M_OLD"],
        }
    )

    reminder, reactivated = await MedicationReminderRepository.find_or_create_reminder(
        user_id="U_PATIENT",
        slot_type="morning",
        creator_user_id="U_FAMILY",
        scheduled_time="08:00",
        collection=col,
    )

    assert reminder.id == "R_FUTURE"
    assert reminder.start_date == _today_date_str()
    assert reactivated is True
    assert len(col.docs) == 1
    assert reminder.medication_ids == ["M_OLD"]
    # 不是使用者自訂的欄位（scheduled_time）沒有被動到。
    assert reminder.scheduled_time == "07:30"


def test_is_schedulable_treats_missing_enabled_as_not_schedulable():
    """`enabled` 欄位缺席時 `_is_schedulable` 必須回傳 False，跟
    `list_active_reminders_up_to_time` 的 exact match `{"enabled": True}`
    對齊——那個查詢不會挑中缺這個欄位的文件。若這裡把缺席當成可排程
    （舊版行為：`doc.get("enabled", True)`），這筆文件會被判斷成「沒問題」
    而永遠不被修補，但排程器其實永遠不會挑中它，變成悄悄失效卻沒人發現。

    目前沒有任何寫入路徑會產生缺 `enabled` 欄位的文件（model 預設值與
    `find_or_create_reminder` 的 `$setOnInsert` 都會補上），這裡是防禦性
    測試：即使未來出現舊資料或新的寫入路徑漏補這個欄位，判斷仍要跟
    查詢站在同一邊，而不是各說各話。
    """
    from app.repositories.medication_repository import _is_schedulable

    doc = {
        "_id": "R_LEGACY",
        "user_id": "U_PATIENT",
        "slot_type": "morning",
        "scheduled_time": "08:00",
        "start_date": "2026-06-01",
        "end_date": None,
        # 刻意不放 enabled 欄位
    }

    assert _is_schedulable(doc, today="2026-08-11") is False


@pytest.mark.asyncio
async def test_find_or_create_reminder_reactivates_a_reminder_missing_enabled_field():
    """既有規則缺 `enabled` 欄位（防禦性情境，見上一個測試）時，也要被當成
    不可排程並真的修好（補上 `enabled: True`），而不是因為判斷邏輯覺得
    「缺欄位＝本來就啟用」就完全不碰它——那樣它會繼續缺這個欄位、繼續被
    `list_active_reminders_up_to_time` 的 exact match 排除在外，永遠不會
    推播，且 `reactivated` 也不會回報 True 讓呼叫端知道要告知使用者。
    """
    from app.repositories.medication_repository import MedicationReminderRepository

    col = _FakeReminderCollection(
        existing_doc={
            "_id": "R_LEGACY",
            "creator_user_id": "U_FAMILY",
            "user_id": "U_PATIENT",
            "slot_type": "morning",
            "scheduled_time": "07:30",
            "start_date": "2026-06-01",
            "end_date": None,
            "medication_ids": ["M_OLD"],
            # 刻意不放 enabled 欄位
        }
    )

    reminder, reactivated = await MedicationReminderRepository.find_or_create_reminder(
        user_id="U_PATIENT",
        slot_type="morning",
        creator_user_id="U_FAMILY",
        scheduled_time="08:00",
        collection=col,
    )

    assert reminder.id == "R_LEGACY"
    assert reminder.enabled is True
    assert reactivated is True
    assert reminder.medication_ids == ["M_OLD"]
    assert reminder.scheduled_time == "07:30"


@pytest.mark.asyncio
async def test_get_reminder_by_id(override_medication_reminders_col):
    col = MagicMock()
    fake_doc = {
        "_id": "R123",
        "creator_user_id": "U_CARE",
        "user_id": "U_PATIENT",
        "slot_type": "morning",
        "scheduled_time": "08:00",
        "start_date": "2026-07-25",
        "enabled": True,
    }
    col.find_one = AsyncMock(return_value=fake_doc)
    override_medication_reminders_col(col)

    res = await MedicationReminderRepository.get_reminder_by_id("R123")
    assert res is not None
    assert res.id == "R123"
    assert res.slot_type == "morning"


@pytest.mark.asyncio
async def test_find_by_ids_queries_reminders_by_id_set():
    """
    給 MedicationScheduler 的批次藥名查表用：一次用 $in 查一批 reminder，
    取代逐筆呼叫 get_reminder_by_id，見 medication_scheduler._TickMedicationNameCache。
    """
    col = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(
        return_value=[
            {
                "_id": "R1",
                "creator_user_id": "U_CARE",
                "user_id": "U_P1",
                "slot_type": "morning",
                "scheduled_time": "08:00",
                "medication_ids": ["M1"],
            },
            {
                "_id": "R2",
                "creator_user_id": "U_CARE",
                "user_id": "U_P2",
                "slot_type": "morning",
                "scheduled_time": "08:00",
            },
        ]
    )
    col.find = MagicMock(return_value=cursor)

    reminders = await MedicationReminderRepository.find_by_ids(
        ["R1", "R2"], collection=col
    )

    assert [r.id for r in reminders] == ["R1", "R2"]
    (query,), _ = col.find.call_args
    assert query == {"_id": {"$in": ["R1", "R2"]}}


@pytest.mark.asyncio
async def test_find_by_ids_with_empty_list_does_not_query_reminders():
    col = MagicMock()
    col.find = MagicMock()

    reminders = await MedicationReminderRepository.find_by_ids([], collection=col)

    assert reminders == []
    col.find.assert_not_called()


def _fake_log_doc(now: datetime) -> dict:
    return {
        "_id": "L123",
        "reminder_id": "R123",
        "user_id": "U_PATIENT",
        "alert_notify_user_id": "U_CARE",
        "slot_type": "morning",
        "scheduled_at": now,
        "timeout_at": now,
        "status": "pending",
    }


def _sample_log(now: datetime) -> MedicationLog:
    return MedicationLog(
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=now,
        timeout_at=now,
    )


@pytest.mark.asyncio
async def test_upsert_log_uses_single_document(override_medication_logs_col):
    col = MagicMock()
    now = datetime.now(tz=timezone.utc)
    col.update_one = AsyncMock(return_value=MagicMock(upserted_id="L123"))
    col.find_one = AsyncMock(return_value=_fake_log_doc(now))
    override_medication_logs_col(col)

    result, created = await MedicationLogRepository.upsert_log(_sample_log(now))

    assert result.id == "L123"
    assert created is True
    col.update_one.assert_awaited_once()
    args, kwargs = col.update_one.await_args
    assert args[0] == {"reminder_id": "R123", "scheduled_at": now}
    assert kwargs.get("upsert") is True


@pytest.mark.asyncio
async def test_upsert_log_reports_not_created_when_already_exists(
    override_medication_logs_col,
):
    """
    created 是「錯過的時段要不要通知家屬」的唯一依據——已存在的 log 必須回報 False，
    否則每個 tick 都會重新發一次彙整通知。
    """
    col = MagicMock()
    now = datetime.now(tz=timezone.utc)
    col.update_one = AsyncMock(return_value=MagicMock(upserted_id=None))
    col.find_one = AsyncMock(return_value=_fake_log_doc(now))
    override_medication_logs_col(col)

    _, created = await MedicationLogRepository.upsert_log(_sample_log(now))

    assert created is False


@pytest.mark.asyncio
async def test_upsert_log_treats_duplicate_key_as_existing(
    override_medication_logs_col,
):
    """
    唯一索引擋下併發插入時，代表另一個實例先建立了這筆 log。
    對本實例而言等同「已存在」，不得回報 created=True 而重複通知。
    """
    col = MagicMock()
    now = datetime.now(tz=timezone.utc)
    col.update_one = AsyncMock(side_effect=DuplicateKeyError("duplicate"))
    col.find_one = AsyncMock(return_value=_fake_log_doc(now))
    override_medication_logs_col(col)

    result, created = await MedicationLogRepository.upsert_log(_sample_log(now))

    assert result.id == "L123"
    assert created is False


@pytest.mark.asyncio
async def test_claim_patient_reminder_guards_on_flag(override_medication_logs_col):
    """
    搶佔必須把「旗標仍為 False」放進 filter，否則兩個實例會各推播一次。
    """
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    override_medication_logs_col(col)

    assert await MedicationLogRepository.claim_patient_reminder("L123") is True
    args, _ = col.update_one.await_args
    assert args[0] == {
        "_id": "L123",
        "status": "pending",
        "patient_reminder_sent": False,
    }
    assert args[1] == {"$set": {"patient_reminder_sent": True}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claim",
    [
        MedicationLogRepository.claim_patient_reminder,
        MedicationLogRepository.claim_patient_urgent_reminder,
        MedicationLogRepository.claim_caregiver_alert,
    ],
)
async def test_claims_require_status_still_pending(claim, override_medication_logs_col):
    """
    三個階段的搶佔都必須把 `status: "pending"` 放進 filter，只看旗標不夠。

    排程器是「查清單 → 逐筆搶佔 → 推播」，清單查出來的那一刻結果就已經過期：
    每一筆的搶佔都排在前面幾筆的 profile 查詢與 LINE 推播之後，使用者完全有
    時間在這段空檔按下「我已用藥」。少了這個條件，會發生
      * T+20：剛確認完的人收到「您尚未點擊我已用藥」；
      * T+30：家屬收到逾時警報，而且 claim 的 `$set` 會把 status 從 taken
        蓋回 missed，連正確的用藥紀錄一起毀掉。
    filter 帶上 status 之後，MongoDB 單一 document 更新的原子性就保證了：
    確認先落地則搶佔失敗（不推播），搶佔先落地則當下確實仍未服藥（警報屬實）。
    """
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    override_medication_logs_col(col)

    await claim("L123")
    args, _ = col.update_one.await_args
    assert args[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_claim_caregiver_alert_does_not_clobber_taken(
    override_medication_logs_col,
):
    """
    搶佔家屬警報時，status 仍是 pending 才算數。

    `release_caregiver_alert` 早就有同一個防呆（見下一個測試），但當時只補了
    還原那一端；搶佔這一端才是實際會把 `status: "missed"` 寫下去的地方，
    漏掉它等於防呆只做了一半——已確認用藥的紀錄仍會被推播流程改回 missed。
    """
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=0))
    override_medication_logs_col(col)

    # 使用者已按下確認（status=taken）→ filter 不成立 → 不取得推播權
    assert await MedicationLogRepository.claim_caregiver_alert("L123") is False
    args, _ = col.update_one.await_args
    assert args[0] == {
        "_id": "L123",
        "status": "pending",
        "caregiver_alert_sent": False,
    }
    assert args[1] == {"$set": {"caregiver_alert_sent": True, "status": "missed"}}


@pytest.mark.asyncio
async def test_claim_patient_reminder_returns_false_when_lost(
    override_medication_logs_col,
):
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=0))
    override_medication_logs_col(col)

    assert await MedicationLogRepository.claim_patient_reminder("L123") is False


@pytest.mark.asyncio
async def test_release_caregiver_alert_does_not_clobber_taken(
    override_medication_logs_col,
):
    """
    還原家屬警報時，status 只能在仍是 missed 的情況下回寫 pending——
    使用者可能在推播失敗的空檔按下「已用藥」，那時 status 是 taken。
    """
    col = MagicMock()
    col.find_one_and_update = AsyncMock(
        return_value={"_id": "L123", "caregiver_alert_attempts": 1}
    )
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    override_medication_logs_col(col)

    assert await MedicationLogRepository.release_caregiver_alert("L123") is True

    # status="missed" 的把關落在累加嘗試次數的那一次條件式更新上：不符合條件
    # 就撈不到文件，後面的還原也不會發生。
    args, _ = col.find_one_and_update.await_args
    assert args[0] == {
        "_id": "L123",
        "caregiver_alert_sent": True,
        "status": "missed",
    }
    set_args, _ = col.update_one.await_args
    assert set_args[1] == {
        "$set": {"caregiver_alert_sent": False, "status": "pending"}
    }


@pytest.mark.asyncio
async def test_mark_as_taken(override_medication_logs_col):
    col = MagicMock()
    now = datetime.now(tz=timezone.utc)
    col.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    col.find_one = AsyncMock(
        return_value={
            "_id": "L123",
            "reminder_id": "R123",
            "user_id": "U_PATIENT",
            "alert_notify_user_id": "U_CARE",
            "slot_type": "morning",
            "scheduled_at": now,
            "timeout_at": now,
            "status": "taken",
            "taken_at": now,
        }
    )
    override_medication_logs_col(col)

    log = await MedicationLogRepository.mark_as_taken("L123", taken_at=now)
    assert log is not None
    assert log.status == "taken"
    assert log.taken_at == now


@pytest.mark.asyncio
async def test_update_reminder_writes_explicit_null_end_date(override_medication_reminders_col):
    """`end_date=None` 必須真的寫進 $set，才有辦法把療程改回「長期」。

    這裡是同一件事的第二道濾網：服務層改用 exclude_unset 之後，資料層原本
    還有一行 `{k: v for k, v in update_data.items() if v is not None}` 會把
    null 再濾掉一次。只修服務層完全沒有效果——兩層都要改，這個測試釘住
    資料層那一半。
    """
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    col.find_one = AsyncMock(
        return_value={
            "_id": "R123",
            "creator_user_id": "U_SELF",
            "user_id": "U_SELF",
            "slot_type": "morning",
            "scheduled_time": "08:00",
            "start_date": "2026-08-01",
            "end_date": None,
            "enabled": True,
        }
    )
    override_medication_reminders_col(col)

    updated = await MedicationReminderRepository.update_reminder("R123", {"end_date": None})

    assert updated is not None
    assert updated.end_date is None
    args, _ = col.update_one.await_args
    set_doc = args[1]["$set"]
    assert "end_date" in set_doc
    assert set_doc["end_date"] is None
    # updated_at 仍然要被戳一下
    assert "updated_at" in set_doc


@pytest.mark.asyncio
async def test_update_reminder_keeps_false_values(override_medication_reminders_col):
    """enabled=False 不能被誤當成「空值」濾掉——關閉提醒靠的就是這個值。"""
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    col.find_one = AsyncMock(
        return_value={
            "_id": "R123",
            "creator_user_id": "U_SELF",
            "user_id": "U_SELF",
            "slot_type": "morning",
            "scheduled_time": "08:00",
            "start_date": "2026-08-01",
            "enabled": False,
        }
    )
    override_medication_reminders_col(col)

    await MedicationReminderRepository.update_reminder("R123", {"enabled": False})

    args, _ = col.update_one.await_args
    assert args[1]["$set"]["enabled"] is False


# --- 藥品（medications）與提醒的關聯 -------------------------------------
# 新增的方法一律以 collection= 注入替身，不使用 monkeypatch。

def _medications_col() -> MagicMock:
    col = MagicMock()
    col.insert_many = AsyncMock()
    col.update_one = AsyncMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])
    col.find = MagicMock(return_value=cursor)
    return col


@pytest.mark.asyncio
async def test_create_many_assigns_ids_and_returns_medications():
    from app.models.medication import Medication
    from app.repositories.medication_repository import MedicationRepository

    col = _medications_col()
    medications = [
        Medication(user_id="U_P", created_by_user_id="U_F", name="藥一"),
        Medication(user_id="U_P", created_by_user_id="U_F", name="藥二"),
    ]

    created = await MedicationRepository.create_many(medications, collection=col)

    assert [medication.name for medication in created] == ["藥一", "藥二"]
    assigned_ids = [medication.id for medication in created]
    assert all(assigned_ids)
    assert len(set(assigned_ids)) == 2
    (documents,), _ = col.insert_many.call_args
    assert [document["_id"] for document in documents] == assigned_ids
    assert [document["name"] for document in documents] == ["藥一", "藥二"]
    assert all(document["_id"] for document in documents)


@pytest.mark.asyncio
async def test_create_many_with_empty_list_does_not_touch_the_database():
    from app.repositories.medication_repository import MedicationRepository

    col = _medications_col()

    created = await MedicationRepository.create_many([], collection=col)

    assert created == []
    col.insert_many.assert_not_called()


@pytest.mark.asyncio
async def test_find_by_ids_queries_by_id_set():
    from app.repositories.medication_repository import MedicationRepository

    col = _medications_col()

    await MedicationRepository.find_by_ids(["M1", "M2"], collection=col)

    (query,), _ = col.find.call_args
    assert query == {"_id": {"$in": ["M1", "M2"]}}


@pytest.mark.asyncio
async def test_find_by_ids_with_empty_list_does_not_query():
    from app.repositories.medication_repository import MedicationRepository

    col = _medications_col()

    found = await MedicationRepository.find_by_ids([], collection=col)

    assert found == []
    col.find.assert_not_called()


@pytest.mark.asyncio
async def test_find_active_by_ids_excludes_disabled_and_out_of_range():
    from app.repositories.medication_repository import MedicationRepository

    col = _medications_col()

    await MedicationRepository.find_active_by_ids(
        ["M1"], "2026-08-09", collection=col
    )

    (query,), _ = col.find.call_args
    assert query["_id"] == {"$in": ["M1"]}
    assert query["enabled"] is True
    conditions = query["$and"]
    assert {"$or": [{"start_date": {"$exists": False}}, {"start_date": {"$lte": "2026-08-09"}}]} in conditions
    assert {
        "$or": [
            {"end_date": None},
            {"end_date": {"$exists": False}},
            {"end_date": {"$gte": "2026-08-09"}},
        ]
    } in conditions


class _FakeMedicationsCollection:
    """真的會依查詢條件過濾的假 medications 集合，用來證明「療程結束後這顆藥
    真的會從 find_active_by_ids 掉出去」，而不只是驗證查詢字典的長相
    （那件事 test_find_active_by_ids_excludes_disabled_and_out_of_range
    已經驗證過了）。比對邏輯與 test_medication_repository 裡驗證
    find_or_create_reminder 用的 _FakeReminderCollection 相同。
    """

    def __init__(self, docs: list[dict]):
        self.docs = docs

    def find(self, query: dict):
        matched = [doc for doc in self.docs if self._matches(doc, query)]
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=matched)
        return cursor

    @classmethod
    def _matches(cls, doc: dict, query: dict) -> bool:
        for key, condition in query.items():
            if key == "$and":
                if not all(cls._matches(doc, clause) for clause in condition):
                    return False
            elif key == "$or":
                if not any(cls._matches(doc, clause) for clause in condition):
                    return False
            elif isinstance(condition, dict):
                for op, value in condition.items():
                    if op == "$exists":
                        if (key in doc) != value:
                            return False
                    elif op == "$lte":
                        if key not in doc or doc[key] is None or doc[key] > value:
                            return False
                    elif op == "$gte":
                        if key not in doc or doc[key] is None or doc[key] < value:
                            return False
                    elif op == "$in":
                        if doc.get(key) not in value:
                            return False
                    else:
                        raise NotImplementedError(op)
            else:
                if doc.get(key) != condition:
                    return False
        return True


@pytest.mark.asyncio
async def test_find_active_by_ids_drops_a_medication_once_its_course_has_ended():
    """對應藥袋掃描的療程換算：5 天的療程算出 end_date 之後，過了那天
    這顆藥就不該再出現在推播的藥品清單裡——這是 find_active_by_ids
    唯一能被觸發的路徑，之前沒有任何呼叫端會傳入已過期的日期組合。
    """
    from app.repositories.medication_repository import MedicationRepository

    col = _FakeMedicationsCollection(
        [
            {
                "_id": "M_EXPIRED",
                "user_id": "U_PATIENT",
                "created_by_user_id": "U_FAMILY",
                "name": "安莫西林",
                "enabled": True,
                "start_date": "2026-08-01",
                "end_date": "2026-08-05",
            },
            {
                "_id": "M_CHRONIC",
                "user_id": "U_PATIENT",
                "created_by_user_id": "U_FAMILY",
                "name": "脈優錠",
                "enabled": True,
                "start_date": "2026-08-01",
                "end_date": None,
            },
        ]
    )

    active_during_course = await MedicationRepository.find_active_by_ids(
        ["M_EXPIRED", "M_CHRONIC"], "2026-08-03", collection=col
    )
    active_after_course = await MedicationRepository.find_active_by_ids(
        ["M_EXPIRED", "M_CHRONIC"], "2026-08-10", collection=col
    )

    assert {m.id for m in active_during_course} == {"M_EXPIRED", "M_CHRONIC"}
    assert {m.id for m in active_after_course} == {"M_CHRONIC"}


@pytest.mark.asyncio
async def test_set_enabled_updates_only_that_medication():
    from app.repositories.medication_repository import MedicationRepository

    col = _medications_col()
    col.update_one.return_value = MagicMock(matched_count=1)

    updated = await MedicationRepository.set_enabled(
        "M1", "U_P", False, collection=col
    )

    assert updated is True
    (query, update), _ = col.update_one.call_args
    assert query == {"_id": "M1", "user_id": "U_P"}
    assert update["$set"]["enabled"] is False


@pytest.mark.asyncio
async def test_link_medications_uses_add_to_set_to_avoid_duplicates():
    from app.repositories.medication_repository import MedicationReminderRepository

    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(matched_count=1))

    linked = await MedicationReminderRepository.link_medications_to_reminder(
        "R1", ["M1", "M2"], collection=col
    )

    assert linked is True
    (query, update), _ = col.update_one.call_args
    assert query == {"_id": "R1"}
    assert update["$addToSet"]["medication_ids"] == {"$each": ["M1", "M2"]}


@pytest.mark.asyncio
async def test_link_medications_with_empty_list_does_not_update():
    from app.repositories.medication_repository import MedicationReminderRepository

    col = MagicMock()
    col.update_one = AsyncMock()

    linked = await MedicationReminderRepository.link_medications_to_reminder(
        "R1", [], collection=col
    )

    assert linked is False
    col.update_one.assert_not_called()


# --- 關閉提醒時註銷當日尚未確認的執行紀錄 ---------------------------------


@pytest.mark.asyncio
async def test_cancel_pending_by_reminder_only_touches_pending_logs():
    """註銷只能打中仍為 pending 的紀錄。

    已經 taken 的不能被改寫——那是使用者真的吃過藥的事實；已經 missed 的也不
    改，家屬警報早就發出去了，事後把它變成「不算漏吃」會讓紀錄與已送出的通知
    互相矛盾。所以查詢條件必須帶 status="pending"，不能只用 reminder_id。
    """
    col = MagicMock()
    col.update_many = AsyncMock(return_value=MagicMock(modified_count=1))

    cancelled = await MedicationLogRepository.cancel_pending_by_reminder(
        "R123", collection=col
    )

    assert cancelled == 1
    (query, update), _ = col.update_many.call_args
    assert query["reminder_id"] == "R123"
    assert query["status"] == "pending"
    assert update["$set"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_pending_by_reminder_returns_zero_when_nothing_pending():
    col = MagicMock()
    col.update_many = AsyncMock(return_value=MagicMock(modified_count=0))

    cancelled = await MedicationLogRepository.cancel_pending_by_reminder(
        "R123", collection=col
    )

    assert cancelled == 0


@pytest.mark.asyncio
async def test_resync_cancels_the_old_time_and_retags_the_same_time():
    """改排程後的對齊：舊時刻註銷，同時刻只改時段名稱。

    展開出來的紀錄是規則在展開當下的快照，三階推播只讀紀錄不回頭 join 規則
    （見 test_push_queries_are_limited_to_pending_status），所以規則改了之後
    那筆紀錄仍會依舊排程走完催促與家屬警報。兩個查詢的條件必須互斥（$ne 與
    相等），否則同一筆紀錄會先被註銷又被改標。
    """
    new_at = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    col = MagicMock()
    col.update_many = AsyncMock(
        side_effect=[MagicMock(modified_count=1), MagicMock(modified_count=0)]
    )

    cancelled, retagged = await MedicationLogRepository.resync_pending_by_reminder(
        "R123", scheduled_at=new_at, slot_type="noon", collection=col
    )

    assert (cancelled, retagged) == (1, 0)
    (cancel_query, cancel_update), _ = col.update_many.call_args_list[0]
    assert cancel_query["reminder_id"] == "R123"
    assert cancel_query["status"] == "pending"
    assert cancel_query["scheduled_at"] == {"$ne": new_at}
    assert cancel_update["$set"]["status"] == "cancelled"

    (retag_query, retag_update), _ = col.update_many.call_args_list[1]
    assert retag_query["reminder_id"] == "R123"
    assert retag_query["status"] == "pending"
    assert retag_query["scheduled_at"] == new_at
    assert retag_query["slot_type"] == {"$ne": "noon"}
    assert retag_update["$set"] == {"slot_type": "noon"}


@pytest.mark.asyncio
async def test_resync_never_touches_taken_or_missed_logs():
    """對齊同樣只能打中 pending。

    已 taken 是使用者真的吃過藥的事實；已 missed 的家屬警報早就送出去了，事後
    改寫會讓紀錄與已送達的通知互相矛盾。改個提醒時間不該回頭改動這兩者——這與
    cancel_pending_by_reminder 是同一條界線。
    """
    col = MagicMock()
    col.update_many = AsyncMock(return_value=MagicMock(modified_count=0))

    await MedicationLogRepository.resync_pending_by_reminder(
        "R123",
        scheduled_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
        slot_type="noon",
        collection=col,
    )

    for (query, _update), _ in col.update_many.call_args_list:
        assert query["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name",
    [
        "list_pending_patient_reminders",
        "list_pending_urgent_reminders",
        "list_pending_caregiver_alerts",
    ],
)
async def test_push_queries_are_limited_to_pending_status(
    override_medication_logs_col, method_name
):
    """三個推播階段的查詢都必須把狀態限定在 pending。

    這是 cancel_pending_by_reminder 之所以能止住後續推播的唯一依據：關閉規則時
    把紀錄移出 pending，這三個查詢就再也挑不到它。哪天有人為了「補漏發」把這裡
    的狀態條件放寬（例如改成 $ne: "taken"），關閉提醒會再次悄悄失效——使用者關掉
    了卻照樣被催、家人照樣收到逾時警報，而且沒有任何測試會紅。
    """
    col = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])
    col.find = MagicMock(return_value=cursor)
    override_medication_logs_col(col)

    await getattr(MedicationLogRepository, method_name)(
        threshold_time=datetime.now(tz=timezone.utc)
    )

    (query,), _ = col.find.call_args
    assert query["status"] == "pending"


@pytest.mark.asyncio
async def test_mark_as_taken_accepts_cancelled_logs(override_medication_logs_col):
    """已註銷的紀錄仍然可以被標記為 taken。

    使用者可能先吃了藥，才進 LIFF 把這個時段關掉（例如療程結束），最後才想起
    來按下推播訊息上還留著的【我已用藥】。真的吃過藥是事實，紀錄應該收斂成
    `taken`，不該因為規則被關掉就永遠停在 `cancelled`。這與 `missed` 允許事後
    轉 `taken` 是同一個判斷：使用者按下的確認一律優先於系統推得的狀態。

    放寬它不會讓推播復活——三階查詢限定 `status="pending"`，`taken` 同樣挑不到。
    """
    now = datetime.now(tz=timezone.utc)
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    col.find_one = AsyncMock(return_value={**_fake_log_doc(now), "status": "taken"})
    override_medication_logs_col(col)

    log = await MedicationLogRepository.mark_as_taken("L123")

    assert log is not None
    assert log.status == "taken"
    (query, _update), _ = col.update_one.call_args
    assert set(query["status"]["$in"]) == {"pending", "missed", "cancelled"}


@pytest.mark.asyncio
async def test_list_logs_by_user_excludes_cancelled(override_medication_logs_col):
    """用藥歷史不列出已註銷的紀錄。

    `cancelled` 是為了擋住排程器在同一天的後續 tick 把紀錄重新 upsert 回
    `pending` 才留下來的內部記帳，不是使用者做過的事。把它一併列出來，使用者
    會在歷史裡看到一筆自己沒有互動過、狀態也無從解讀的紀錄（前端沒有對應的
    標籤，只會顯示成空白或原始字串）。
    """
    col = MagicMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=[])
    col.find = MagicMock(return_value=cursor)
    override_medication_logs_col(col)

    await MedicationLogRepository.list_logs_by_user("U_SELF")

    (query,), _ = col.find.call_args
    assert query["user_id"] == "U_SELF"
    assert query["status"] == {"$ne": "cancelled"}


# ── 推播重試上限 ────────────────────────────────────────────────────
#
# release_* 把旗標還回去讓下一個 tick 重試，這對瞬時故障是對的；但對 LINE 月額度
# 耗盡的 429 這類不會自行恢復的錯誤，等於每 60 秒重試到月底。以下驗證嘗試次數
# 會累加、達到上限就放棄，以及缺欄位的舊紀錄不會第一次就被判定為已達上限。


@pytest.mark.asyncio
async def test_release_patient_reminder_increments_and_retries(
    override_medication_logs_col,
):
    col = MagicMock()
    col.find_one_and_update = AsyncMock(
        return_value={"_id": "L1", "patient_reminder_attempts": 1}
    )
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    override_medication_logs_col(col)

    assert await MedicationLogRepository.release_patient_reminder("L1") is True

    inc_args, inc_kwargs = col.find_one_and_update.await_args
    assert inc_args[0] == {"_id": "L1", "patient_reminder_sent": True}
    assert inc_args[1] == {"$inc": {"patient_reminder_attempts": 1}}
    assert inc_kwargs["return_document"] is ReturnDocument.AFTER
    set_args, _ = col.update_one.await_args
    assert set_args[1] == {"$set": {"patient_reminder_sent": False}}


@pytest.mark.asyncio
async def test_release_gives_up_at_attempt_cap(override_medication_logs_col):
    """達到上限就不再還原旗標，該階段就此放棄，不會每 60 秒重試到月底。"""
    from app.repositories.medication_repository import MAX_PUSH_ATTEMPTS

    col = MagicMock()
    col.find_one_and_update = AsyncMock(
        return_value={"_id": "L1", "urgent_reminder_attempts": MAX_PUSH_ATTEMPTS}
    )
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    override_medication_logs_col(col)

    assert await MedicationLogRepository.release_patient_urgent_reminder("L1") is False
    col.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_release_on_legacy_log_without_attempts_field(
    override_medication_logs_col,
):
    """沒有嘗試次數欄位的既有紀錄，第一次失敗仍必須重試。

    這是先 $inc 再判斷、而不是用單一條件式更新的理由：`{"$lt": N}` 對
    「欄位不存在」不成立，會讓所有既有紀錄第一次就被判定為已達上限。
    """
    col = MagicMock()
    # $inc 對缺席欄位視為 0，回寫後是 1。
    col.find_one_and_update = AsyncMock(
        return_value={"_id": "L_OLD", "patient_reminder_attempts": 1}
    )
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    override_medication_logs_col(col)

    assert await MedicationLogRepository.release_patient_reminder("L_OLD") is True


@pytest.mark.asyncio
async def test_release_returns_false_when_log_no_longer_matches(
    override_medication_logs_col,
):
    """紀錄已不符合還原條件（例如使用者在空檔按下已用藥）時不做任何寫入。"""
    col = MagicMock()
    col.find_one_and_update = AsyncMock(return_value=None)
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    override_medication_logs_col(col)

    assert await MedicationLogRepository.release_caregiver_alert("L1") is False
    col.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_pending_by_reminder_ids_batches_with_date_floor():
    col = MagicMock()
    col.update_many = AsyncMock(return_value=MagicMock(modified_count=3))
    floor = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)

    result = await MedicationLogRepository.cancel_pending_by_reminder_ids(
        ["R1", "R2"], scheduled_from=floor, collection=col
    )

    assert result == 3
    args, _ = col.update_many.await_args
    assert args[0] == {
        "reminder_id": {"$in": ["R1", "R2"]},
        "status": "pending",
        "scheduled_at": {"$gte": floor},
    }
    assert args[1] == {"$set": {"status": "cancelled"}}


@pytest.mark.asyncio
async def test_cancel_pending_by_reminder_ids_skips_empty_input():
    """沒有規則要作廢時不發出查詢——這是每 60 秒一次的迴圈上的路徑。"""
    col = MagicMock()
    col.update_many = AsyncMock()

    assert await MedicationLogRepository.cancel_pending_by_reminder_ids(
        [], collection=col
    ) == 0
    col.update_many.assert_not_awaited()


# ── medical-news-push：藥品鍵查詢 ──────────────────────────────────


def _news_collection(names=None, generics=None, docs=None):
    from unittest.mock import AsyncMock, MagicMock

    collection = MagicMock()

    async def _distinct(field, query=None):
        return {"name": list(names or []), "generic_name": list(generics or [])}[field]

    collection.distinct = AsyncMock(side_effect=_distinct)
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=list(docs or []))
    collection.find = MagicMock(return_value=cursor)
    return collection


@pytest.mark.asyncio
async def test_list_active_drug_keys_unions_name_and_generic_name():
    collection = _news_collection(
        names=["普拿疼", "冠脂妥"], generics=["ACETAMINOPHEN", "普拿疼"]
    )

    keys = await MedicationRepository.list_active_drug_keys(
        "2026-09-02", collection=collection
    )

    assert set(keys) == {"普拿疼", "冠脂妥", "ACETAMINOPHEN"}


@pytest.mark.asyncio
async def test_list_active_drug_keys_drops_blank_values():
    collection = _news_collection(names=["普拿疼", "", None], generics=["  "])

    keys = await MedicationRepository.list_active_drug_keys(
        "2026-09-02", collection=collection
    )

    assert keys == ["普拿疼"]


@pytest.mark.asyncio
async def test_list_active_drug_keys_excludes_expired_course():
    collection = _news_collection(names=[], generics=[])

    await MedicationRepository.list_active_drug_keys(
        "2026-09-02", collection=collection
    )

    query = collection.distinct.call_args.args[1]
    assert query["enabled"] is True
    assert query["$and"] == _active_date_window("2026-09-02")


@pytest.mark.asyncio
async def test_list_active_drug_keys_has_no_user_filter():
    """索引服務要的是全體不重複藥名，與是誰在吃無關（design 決策 2）。

    若哪天有人為了「順便」加上 user_id，按藥快取的前提就沒了——快取會退化成
    按人快取，成本重新變成 O(使用者數)。
    """
    collection = _news_collection(names=[], generics=[])

    await MedicationRepository.list_active_drug_keys(
        "2026-09-02", collection=collection
    )

    query = collection.distinct.call_args.args[1]
    assert "user_id" not in query


@pytest.mark.asyncio
async def test_list_active_by_user_respects_date_window():
    collection = _news_collection(docs=[])

    await MedicationRepository.list_active_by_user(
        "U1", "2026-09-02", collection=collection
    )

    query = collection.find.call_args.args[0]
    assert query["user_id"] == "U1"
    assert query["enabled"] is True
    assert query["$and"] == _active_date_window("2026-09-02")


@pytest.mark.asyncio
async def test_list_active_by_user_parses_documents():
    collection = _news_collection(
        docs=[
            {
                "_id": "m1",
                "user_id": "U1",
                "created_by_user_id": "U1",
                "name": "普拿疼",
                "start_date": "2026-09-01",
            }
        ]
    )

    result = await MedicationRepository.list_active_by_user(
        "U1", "2026-09-02", collection=collection
    )

    assert len(result) == 1
    assert result[0].name == "普拿疼"
