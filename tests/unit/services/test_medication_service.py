import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import HTTPException

from app.models.family_tree import FamilyMember, FamilyTree
from app.models.medication import (
    TAIPEI_TZ,
    CreateMedicationRequest,
    CreateMedicationReminderRequest,
    Medication,
    MedicationLog,
    MedicationReminder,
    ReminderEntryInput,
    UpdateMedicationReminderRequest,
)
from app.services.medication.medication_service import MedicationService


class FakeMedicationRepository:
    """`get_user_reminders_with_medications` 用建構子注入的替身——不必碰
    MongoDB，也不需要 monkeypatch 掉整個 MedicationRepository。

    後續任務（建立／更新提醒的藥品歸屬驗證、逐藥確認的有效性判定、藥品的
    列出與手動新增）陸續用到同一個替身的更多方法，都收在這裡，理由與
    `find_by_ids` 相同：不必碰 MongoDB。
    """

    def __init__(self, medications: list[Medication] | None = None):
        self._medications = medications or []
        self.queried_ids: list[str] | None = None
        self.active_queried_ids: list[str] | None = None
        self.active_queried_date: str | None = None
        self.created_medications: list[Medication] = []

    async def find_by_ids(self, medication_ids: list[str]) -> list[Medication]:
        self.queried_ids = list(medication_ids)
        return [m for m in self._medications if m.id in medication_ids]

    async def find_active_by_ids(
        self, medication_ids: list[str], date_str: str
    ) -> list[Medication]:
        self.active_queried_ids = list(medication_ids)
        self.active_queried_date = date_str
        return [
            m
            for m in self._medications
            if m.id in medication_ids and m.enabled
        ]

    async def list_by_user(self, user_id: str) -> list[Medication]:
        return [m for m in self._medications if m.user_id == user_id]

    async def create_one(self, medication: Medication) -> Medication:
        saved = medication.model_copy(
            update={"id": medication.id or f"M_NEW_{len(self.created_medications)}"}
        )
        self.created_medications.append(saved)
        return saved


class _FakeReminderCursor:
    def __init__(self, documents: list[dict]):
        self._documents = documents

    async def to_list(self, length=None):
        return [dict(doc) for doc in self._documents]


class _FakeReminderCollection:
    """`list_reminders_by_user` 現在支援 `collection=` 注入（沿用本檔案其他
    repository 方法一貫的慣例），這裡模擬它唯一用到的 `find(...).to_list(...)`
    介面，不需要 patch 掉整個 staticmethod。"""

    def __init__(self, documents: list[dict]):
        self._documents = documents

    def find(self, query: dict):
        matched = [d for d in self._documents if d.get("user_id") == query.get("user_id")]
        return _FakeReminderCursor(matched)


@pytest.fixture()
def medication_service():
    return MedicationService()


@pytest.mark.asyncio
async def test_create_reminders_for_self():
    req = CreateMedicationReminderRequest(
        user_id="U_SELF",
        slots=["morning", "evening"],
        start_date="2026-07-26",
    )
    reminders_repo = FakeReminderRepository(reminder=None, siblings=[])
    service = MedicationService(reminder_repository=reminders_repo)

    reminders = await service.create_reminders(creator_user_id="U_SELF", request=req)

    assert len(reminders) == 2
    assert reminders[0].slot_type == "morning"
    assert reminders[0].scheduled_time == "08:00"
    assert reminders[1].slot_type == "evening"
    assert reminders[1].scheduled_time == "18:00"


@pytest.mark.asyncio
async def test_create_reminders_for_family_member():
    req = CreateMedicationReminderRequest(
        user_id="U_MEMBER",
        slots=["noon"],
        start_date="2026-07-26",
    )
    # 族譜檢查已移出服務層：為他人建立提醒的授權由 router 經
    # FamilyAuthorizationService 判定（GENERAL 寫入權），拒絕的情境由
    # tests/unit/routers/test_medications_authorization.py 覆蓋。
    reminders_repo = FakeReminderRepository(reminder=None, siblings=[])
    service = MedicationService(reminder_repository=reminders_repo)

    reminders = await service.create_reminders(creator_user_id="U_CARE", request=req)

    assert len(reminders) == 1
    assert reminders[0].user_id == "U_MEMBER"
    assert reminders[0].slot_type == "noon"


@pytest.mark.asyncio
async def test_create_reminders_conflicting_slot_is_rejected():
    """請求的任一時段已有規則時整批擋下，不建立任何規則（spec「提醒規則與
    用藥對象」）。現況是靜默建第二筆，本 change 收緊成 409。"""
    existing = MedicationReminder(
        _id="R_EXIST",
        creator_user_id="U_SELF",
        user_id="U_SELF",
        slot_type="morning",
        scheduled_time="08:00",
    )
    reminders_repo = FakeReminderRepository(reminder=None, siblings=[existing])
    service = MedicationService(reminder_repository=reminders_repo)
    req = CreateMedicationReminderRequest(
        user_id="U_SELF", slots=["morning", "evening"]
    )

    with pytest.raises(HTTPException) as excinfo:
        await service.create_reminders(creator_user_id="U_SELF", request=req)

    assert excinfo.value.status_code == 409
    assert "早" in excinfo.value.detail
    assert reminders_repo.created_reminders == []


@pytest.mark.asyncio
async def test_create_reminders_with_slot_entries_derives_fields():
    """slot_entries 有給的時段以它為準：派生欄位（scheduled_time／
    timeout_anchor_time／medication_ids）由條目重算，不是沿用單一時刻的
    舊行為（design 決策 1、2）。"""
    fake_medications = FakeMedicationRepository(
        [
            Medication(id="M1", user_id="U_SELF", created_by_user_id="U_SELF", name="降血糖藥"),
            Medication(id="M2", user_id="U_SELF", created_by_user_id="U_SELF", name="血壓藥"),
        ]
    )
    reminders_repo = FakeReminderRepository(reminder=None, siblings=[])
    service = MedicationService(
        reminder_repository=reminders_repo, medication_repository=fake_medications
    )
    req = CreateMedicationReminderRequest(
        user_id="U_SELF",
        slots=["morning"],
        slot_entries={
            "morning": [
                ReminderEntryInput(
                    meal_timing="before_meal", scheduled_time="07:30", medication_ids=["M1"]
                ),
                ReminderEntryInput(
                    meal_timing="after_meal", scheduled_time="08:30", medication_ids=["M2"]
                ),
            ]
        },
    )

    reminders = await service.create_reminders(creator_user_id="U_SELF", request=req)

    assert len(reminders) == 1
    reminder = reminders[0]
    assert reminder.scheduled_time == "07:30"
    assert reminder.timeout_anchor_time == "08:30"
    assert reminder.medication_ids == ["M1", "M2"]
    assert [e.meal_timing for e in reminder.entries] == ["before_meal", "after_meal"]


@pytest.mark.asyncio
async def test_create_reminders_rejects_medication_belonging_to_other_user():
    """條目掛的藥品若不屬於這位用藥者，回 400，不建立規則（spec
    「EntryInput.medication_ids 必須全部屬於該用藥者」）。"""
    fake_medications = FakeMedicationRepository(
        [Medication(id="M1", user_id="U_OTHER", created_by_user_id="U_OTHER", name="別人的藥")]
    )
    reminders_repo = FakeReminderRepository(reminder=None, siblings=[])
    service = MedicationService(
        reminder_repository=reminders_repo, medication_repository=fake_medications
    )
    req = CreateMedicationReminderRequest(
        user_id="U_SELF",
        slots=["morning"],
        slot_entries={
            "morning": [
                ReminderEntryInput(meal_timing="none", scheduled_time="08:00", medication_ids=["M1"])
            ]
        },
    )

    with pytest.raises(HTTPException) as excinfo:
        await service.create_reminders(creator_user_id="U_SELF", request=req)

    assert excinfo.value.status_code == 400
    assert reminders_repo.created_reminders == []


@pytest.mark.asyncio
async def test_create_reminders_validates_all_slots_before_writing_any():
    """後面某個時段的藥品驗證失敗時，前面的時段不該先被寫入。

    否則使用者收到 400 後重試整個請求，前面那個時段會撞上剛剛才建立的規則
    而變成 409——這個端點就再也無法用來建立那個時段了。驗證必須在任何一筆
    `create_reminder` 呼叫之前，對全部時段的條目一次做完。
    """
    fake_medications = FakeMedicationRepository(
        [Medication(id="M9", user_id="U_OTHER", created_by_user_id="U_OTHER", name="別人的藥")]
    )
    reminders_repo = FakeReminderRepository(reminder=None, siblings=[])
    service = MedicationService(
        reminder_repository=reminders_repo, medication_repository=fake_medications
    )
    req = CreateMedicationReminderRequest(
        user_id="U_SELF",
        slots=["morning", "evening"],
        slot_entries={
            "evening": [
                ReminderEntryInput(
                    meal_timing="none", scheduled_time="18:00", medication_ids=["M9"]
                )
            ]
        },
    )

    with pytest.raises(HTTPException) as excinfo:
        await service.create_reminders(creator_user_id="U_SELF", request=req)

    assert excinfo.value.status_code == 400
    # 「早」時段的條目完全合法，若驗證是逐時段邊做邊寫，這裡會先被建立成功。
    assert reminders_repo.created_reminders == []


@pytest.mark.asyncio
async def test_create_reminders_deduplicates_repeated_slots():
    """同一次請求重複勾選同一個時段（例如手滑點兩下）不該建立兩筆規則
    ——那正是 409 檢查要防止的事，重複的時段必須先去重才逐一比對與建立。"""
    reminders_repo = FakeReminderRepository(reminder=None, siblings=[])
    service = MedicationService(reminder_repository=reminders_repo)
    req = CreateMedicationReminderRequest(user_id="U_SELF", slots=["morning", "morning"])

    reminders = await service.create_reminders(creator_user_id="U_SELF", request=req)

    assert len(reminders) == 1
    assert len(reminders_repo.created_reminders) == 1


def test_medication_service_no_longer_hand_writes_family_checks():
    """服務層 SHALL NOT 自行判斷「他是不是家人」。

    「在族譜裡＝有權」正是本次授權改動要消滅的語意，它比權限矩陣寬。留一份
    在這裡就會有人以為它還是授權依據，於是同一個問題有兩個答案。授權一律由
    router 經 FamilyAuthorizationService 判定，拒絕的情境由
    tests/unit/routers/test_medications_authorization.py 覆蓋。
    """
    import inspect

    from app.services.medication import medication_service as module

    source = inspect.getsource(module)
    assert "FamilyTreeRepository" not in source
    assert "family_members" not in source


def _reminder_without_medications() -> MedicationReminder:
    """整批確認測試用的規則替身：沒有掛任何藥品，`_expected_medication_ids`
    不需要真的查資料庫就能算出空集合，測試才能只關注確認流程本身。"""
    return MedicationReminder(
        _id="R123",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
    )


@pytest.mark.asyncio
async def test_confirm_medication_success():
    fake_log = MedicationLog(
        id="L123",
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-07-26T08:00:00Z",
        timeout_at="2026-07-26T08:30:00Z",
        status="pending",
    )
    service = MedicationService(
        log_repository=FakeLogRepository(log=fake_log),
        reminder_repository=FakeReminderRepository(reminder=_reminder_without_medications()),
    )

    res = await service.confirm_medication(log_id="L123", user_id="U_PATIENT")

    assert res.status == "taken"


@pytest.mark.asyncio
async def test_confirm_medication_forbidden_for_other_user():
    fake_log = MedicationLog(
        id="L123",
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-07-26T08:00:00Z",
        timeout_at="2026-07-26T08:30:00Z",
    )
    service = MedicationService(log_repository=FakeLogRepository(log=fake_log))

    with pytest.raises(HTTPException) as excinfo:
        await service.confirm_medication(log_id="L123", user_id="U_OTHER")
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_confirm_medication_missed_status_update():
    missed_log = MedicationLog(
        id="L123",
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-07-26T08:00:00Z",
        timeout_at="2026-07-26T08:30:00Z",
        status="missed",
    )
    service = MedicationService(
        log_repository=FakeLogRepository(log=missed_log),
        reminder_repository=FakeReminderRepository(reminder=_reminder_without_medications()),
    )

    res = await service.confirm_medication(log_id="L123", user_id="U_PATIENT")

    assert res.status == "taken"


@pytest.mark.asyncio
async def test_confirm_medication_writes_all_expected_ids_on_bulk_confirm():
    """整批確認（不帶 medication_id）要把當下有效的藥品全部寫進
    taken_medication_ids，讓用藥歷史能一致地回答「那次吃了什麼」（design
    決策 4）。"""
    log = MedicationLog(
        id="L123",
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-08-09T00:00:00Z",
        timeout_at="2026-08-09T00:30:00Z",
        status="pending",
    )
    reminder = MedicationReminder(
        _id="R123",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        medication_ids=["M1", "M2"],
    )
    fake_medications = FakeMedicationRepository(
        [
            _medication("M1", "脈優"),
            _medication("M2", "利尿劑"),
        ]
    )
    log_repo = FakeLogRepository(log=log)
    service = MedicationService(
        log_repository=log_repo,
        reminder_repository=FakeReminderRepository(reminder=reminder),
        medication_repository=fake_medications,
    )

    result = await service.confirm_medication(log_id="L123", user_id="U_PATIENT")

    assert result.status == "taken"
    assert set(result.taken_medication_ids) == {"M1", "M2"}
    assert log_repo.mark_as_taken_calls[0]["taken_medication_ids"] == ["M1", "M2"]


class _RaisingMedicationRepository(FakeMedicationRepository):
    """`find_active_by_ids` 一律拋例外的替身，模擬查詢當下 DB 抖動或其他
    非預期錯誤——用來驗證整批確認與逐藥確認對這類失敗的不同容忍度。"""

    async def find_active_by_ids(self, medication_ids: list[str], date_str: str):
        raise RuntimeError("模擬查詢有效藥品失敗")


@pytest.mark.asyncio
async def test_confirm_medication_bulk_survives_expected_lookup_failure():
    """整批確認（【全部已服用】）不應該因為查詢有效藥品失敗而讓紀錄卡在
    pending——那會讓家屬之後收到一次子虛烏有的漏吃藥警報。這與逐藥確認刻意
    不吞例外（`_expected_medication_ids` 的判定會直接決定狀態轉換）是不同的
    風險等級：整批確認的轉換由使用者明確按下的動作決定，expected 只是用來
    填 `taken_medication_ids` 讓歷史好看，查不到就寫空清單也不影響本次確認
    是否該完成。"""
    log = MedicationLog(
        id="L123",
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-08-09T00:00:00Z",
        timeout_at="2026-08-09T00:30:00Z",
        status="pending",
    )
    reminder = MedicationReminder(
        _id="R123",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        medication_ids=["M1", "M2"],
    )
    log_repo = FakeLogRepository(log=log)
    service = MedicationService(
        log_repository=log_repo,
        reminder_repository=FakeReminderRepository(reminder=reminder),
        medication_repository=_RaisingMedicationRepository(
            [_medication("M1", "脈優"), _medication("M2", "利尿劑")]
        ),
    )

    result = await service.confirm_medication(log_id="L123", user_id="U_PATIENT")

    assert result.status == "taken"
    assert log_repo.mark_as_taken_calls == [
        {"log_id": "L123", "taken_medication_ids": []}
    ]


@pytest.mark.asyncio
async def test_confirm_medication_per_drug_still_raises_on_lookup_failure():
    """逐藥確認維持嚴格：到齊判定就是靠 `_expected_medication_ids` 的結果
    決定要不要收尾成 taken，查詢失敗時吞掉例外、悄悄把 expected 當空清單，
    會讓任何一次逐藥確認都被誤判成「全部到齊」而錯誤標記已服藥——寧可讓
    這次確認失敗（拋出例外），也不要留下錯誤的用藥紀錄。"""
    log = MedicationLog(
        id="L123",
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-08-09T00:00:00Z",
        timeout_at="2026-08-09T00:30:00Z",
        status="pending",
    )
    reminder = MedicationReminder(
        _id="R123",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        medication_ids=["M1", "M2"],
    )
    log_repo = FakeLogRepository(log=log)
    service = MedicationService(
        log_repository=log_repo,
        reminder_repository=FakeReminderRepository(reminder=reminder),
        medication_repository=_RaisingMedicationRepository(
            [_medication("M1", "脈優"), _medication("M2", "利尿劑")]
        ),
    )

    with pytest.raises(RuntimeError):
        await service.confirm_medication(
            log_id="L123", user_id="U_PATIENT", medication_id="M1"
        )


@pytest.mark.asyncio
async def test_create_reminders_custom_slot_times():
    req = CreateMedicationReminderRequest(
        user_id="U_SELF",
        slots=["morning", "evening"],
        slot_times={"morning": "07:30", "evening": "19:00"},
        start_date="2026-07-29",
    )
    reminders_repo = FakeReminderRepository(reminder=None, siblings=[])
    service = MedicationService(reminder_repository=reminders_repo)

    reminders = await service.create_reminders(creator_user_id="U_SELF", request=req)

    assert len(reminders) == 2
    assert reminders[0].scheduled_time == "07:30"
    assert reminders[1].scheduled_time == "19:00"


@pytest.mark.asyncio
async def test_get_user_reminders_family_permission(medication_service):
    fake_tree = FamilyTree(
        user_id="U_CARE",
        family_members=[FamilyMember(user_id="U_MEMBER")],
        created_at="2026-07-26T00:00:00Z",
        updated_at="2026-07-26T00:00:00Z",
    )
    # 服務層不再自行判斷族譜；讀取授權（GENERAL 讀取權）由 router 完成。
    # 這裡只驗它把 user_id 原樣帶到資料層。
    with patch(
        "app.services.medication.medication_service.MedicationReminderRepository.list_reminders_by_user",
        new_callable=AsyncMock,
        return_value=[],
    ) as mock_list:
        reminders = await medication_service.get_user_reminders(
            "U_MEMBER", requester_user_id="U_CARE"
        )
        assert reminders == []
        mock_list.assert_awaited_once_with("U_MEMBER")


@pytest.mark.asyncio
async def test_get_user_reminders_with_medications_resolves_medication_ids():
    fake_medications = FakeMedicationRepository(
        [
            Medication(
                id="M1",
                user_id="U_SELF",
                created_by_user_id="U_SELF",
                name="脈優錠",
            ),
            Medication(
                id="M2",
                user_id="U_SELF",
                created_by_user_id="U_SELF",
                name="抗生素",
            ),
        ]
    )
    service = MedicationService(medication_repository=fake_medications)
    fake_collection = _FakeReminderCollection(
        [
            {
                "_id": "R1",
                "creator_user_id": "U_SELF",
                "user_id": "U_SELF",
                "slot_type": "morning",
                "scheduled_time": "08:00",
                "medication_ids": ["M1", "M_MISSING"],
            }
        ]
    )

    result = await service.get_user_reminders_with_medications(
        "U_SELF", reminder_collection=fake_collection
    )

    assert len(result) == 1
    # 缺席的 id（可能是資料不一致或藥品剛好被刪）直接濾掉，不讓呼叫端拿到
    # 一個對不到任何實際藥品的殘影 id。
    assert [m.id for m in result[0].medications] == ["M1"]
    assert fake_medications.queried_ids == ["M1", "M_MISSING"]


@pytest.mark.asyncio
async def test_get_user_reminders_with_medications_empty_when_no_medication_ids():
    fake_medications = FakeMedicationRepository()
    service = MedicationService(medication_repository=fake_medications)
    fake_collection = _FakeReminderCollection(
        [
            {
                "_id": "R1",
                "creator_user_id": "U_SELF",
                "user_id": "U_SELF",
                "slot_type": "evening",
                "scheduled_time": "18:00",
            }
        ]
    )

    result = await service.get_user_reminders_with_medications(
        "U_SELF", reminder_collection=fake_collection
    )

    assert result[0].medications == []
    # 沒有任何 medication_ids 時不必查資料庫——find_by_ids 對空清單本來就會
    # 直接回空，但這裡驗證呼叫確實發生過（傳入空清單），而不是被跳過導致
    # medications 欄位維持未初始化的狀態。
    assert fake_medications.queried_ids == []


@pytest.mark.asyncio
async def test_get_user_reminders_with_medications_resolves_thumbnail_url():
    """縮圖 URL 在讀取當下就地解析，不是資料庫裡本來就存的值（見
    Medication.thumbnail_url 的欄位註解）。只有 license_number 已確定的
    藥品才會呼叫解析器——這與 medication_scheduler._resolve_thumbnail
    同一條規則（spec「證號不確定時不得顯示藥丸照片」）。"""
    fake_medications = FakeMedicationRepository(
        [
            Medication(
                id="M1",
                user_id="U_SELF",
                created_by_user_id="U_SELF",
                name="脈優錠",
                license_number="LIC-1",
            ),
            Medication(
                id="M2",
                user_id="U_SELF",
                created_by_user_id="U_SELF",
                name="抗生素",
                license_number=None,
            ),
        ]
    )
    resolved_urls = {"LIC-1": "https://example.com/drug-appearance/abc.jpg"}
    calls: list[str] = []

    def fake_resolver(license_number: str):
        calls.append(license_number)
        return resolved_urls.get(license_number)

    service = MedicationService(
        medication_repository=fake_medications, appearance_image_resolver=fake_resolver
    )
    fake_collection = _FakeReminderCollection(
        [
            {
                "_id": "R1",
                "creator_user_id": "U_SELF",
                "user_id": "U_SELF",
                "slot_type": "morning",
                "scheduled_time": "08:00",
                "medication_ids": ["M1", "M2"],
            }
        ]
    )

    result = await service.get_user_reminders_with_medications(
        "U_SELF", reminder_collection=fake_collection
    )

    by_id = {m.id: m for m in result[0].medications}
    assert by_id["M1"].thumbnail_url == "https://example.com/drug-appearance/abc.jpg"
    assert by_id["M2"].thumbnail_url is None
    # license_number 未確定的藥品不該白白呼叫一次解析器。
    assert calls == ["LIC-1"]


@pytest.mark.asyncio
async def test_get_user_reminders_with_medications_thumbnail_resolution_failure_degrades_to_none():
    """解析器本身出例外（例如未來換掉實作）不能讓整批查詢連坐失敗，
    退化成沒有縮圖即可（spec「照片缺席時的降級」）。"""
    fake_medications = FakeMedicationRepository(
        [
            Medication(
                id="M1",
                user_id="U_SELF",
                created_by_user_id="U_SELF",
                name="脈優錠",
                license_number="LIC-1",
            ),
        ]
    )

    def broken_resolver(license_number: str):
        raise RuntimeError("boom")

    service = MedicationService(
        medication_repository=fake_medications, appearance_image_resolver=broken_resolver
    )
    fake_collection = _FakeReminderCollection(
        [
            {
                "_id": "R1",
                "creator_user_id": "U_SELF",
                "user_id": "U_SELF",
                "slot_type": "morning",
                "scheduled_time": "08:00",
                "medication_ids": ["M1"],
            }
        ]
    )

    result = await service.get_user_reminders_with_medications(
        "U_SELF", reminder_collection=fake_collection
    )

    assert result[0].medications[0].thumbnail_url is None


class _FakeActiveMedicationRepository:
    """`list_medication_names_for_log` 用建構子注入的替身。

    刻意與 FakeMedicationRepository 分開：那個替身模擬的是 `find_by_ids`
    （LIFF 要看到全部關聯藥品，含已停用的），這裡模擬的是 `find_active_by_ids`
    （推播只能列出當日仍有效的藥）。兩者的語意不同，共用一個替身會讓「推播
    是否真的走了有效性篩選」這件事測不出來。
    """

    def __init__(self, medications: list[Medication] | None = None):
        self._medications = medications or []
        self.queried_ids: list[str] | None = None
        self.queried_date: str | None = None

    async def find_active_by_ids(
        self, medication_ids: list[str], date_str: str
    ) -> list[Medication]:
        self.queried_ids = list(medication_ids)
        self.queried_date = date_str
        return [m for m in self._medications if m.id in medication_ids]


def _medication(medication_id: str, name: str) -> Medication:
    return Medication(
        id=medication_id,
        user_id="U_PATIENT",
        created_by_user_id="U_CARE",
        name=name,
    )


def _log_for_names(scheduled_at: str = "2026-08-09T00:00:00Z") -> MedicationLog:
    return MedicationLog(
        id="L123",
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at=scheduled_at,
        timeout_at="2026-08-09T00:30:00Z",
        status="taken",
    )


@pytest.mark.asyncio
async def test_list_medication_names_for_log_preserves_reminder_order():
    """藥名順序沿用 `reminder.medication_ids`，與排程器的批次版本一致。

    兩條路徑（排程器推播 / 使用者按確認）顯示的是同一個時段的同一批藥，順序
    不一致會讓使用者以為是兩份不同的清單。
    """
    fake_repo = _FakeActiveMedicationRepository(
        [_medication("M2", "利尿劑"), _medication("M1", "脈優")]
    )
    service = MedicationService(medication_repository=fake_repo)
    reminder = MedicationReminder(
        id="R123",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        medication_ids=["M1", "M2"],
    )

    with patch(
        "app.services.medication.medication_service.MedicationReminderRepository.get_reminder_by_id",
        new_callable=AsyncMock,
        return_value=reminder,
    ):
        names = await service.list_medication_names_for_log(_log_for_names())

    assert names == ["脈優", "利尿劑"]


@pytest.mark.asyncio
async def test_list_medication_names_for_log_uses_the_logs_own_taipei_date():
    """有效性以 log 自己的台北日期判定，不是「今天」。

    確認可能發生在跨日之後（例如睡前那一劑拖到隔天凌晨才按），用今天的日期
    去篩會把當時仍有效、今天才結束療程的藥錯誤地濾掉。排程器的
    `_TickMedicationNameCache` 也是這個規則，兩邊必須算出同一個答案。
    """
    fake_repo = _FakeActiveMedicationRepository([_medication("M1", "脈優")])
    service = MedicationService(medication_repository=fake_repo)
    reminder = MedicationReminder(
        id="R123",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="bedtime",
        medication_ids=["M1"],
    )

    # UTC 2026-08-09 13:30 = 台北 2026-08-09 21:30
    with patch(
        "app.services.medication.medication_service.MedicationReminderRepository.get_reminder_by_id",
        new_callable=AsyncMock,
        return_value=reminder,
    ):
        await service.list_medication_names_for_log(
            _log_for_names(scheduled_at="2026-08-09T13:30:00Z")
        )

    assert fake_repo.queried_date == "2026-08-09"


@pytest.mark.asyncio
async def test_list_medication_names_for_log_returns_empty_without_medication_ids():
    """既有規則的 medication_ids 是空陣列——不查資料庫，卡片退回原本的版面。"""
    fake_repo = _FakeActiveMedicationRepository()
    service = MedicationService(medication_repository=fake_repo)
    reminder = MedicationReminder(
        id="R123",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
    )

    with patch(
        "app.services.medication.medication_service.MedicationReminderRepository.get_reminder_by_id",
        new_callable=AsyncMock,
        return_value=reminder,
    ):
        names = await service.list_medication_names_for_log(_log_for_names())

    assert names == []
    assert fake_repo.queried_ids is None


@pytest.mark.asyncio
async def test_list_medication_names_for_log_swallows_lookup_failures():
    """查詢失敗只回空清單，不得往外拋。

    這個查詢純粹是卡片上的補充資訊，而呼叫端是「使用者剛按下我已用藥」的
    回覆路徑——用藥已經確認成功了，不能因為查不到藥名就讓他看到錯誤訊息、
    以為剛才那一下沒有被記錄到。
    """
    service = MedicationService(medication_repository=_FakeActiveMedicationRepository())

    with patch(
        "app.services.medication.medication_service.MedicationReminderRepository.get_reminder_by_id",
        new_callable=AsyncMock,
        side_effect=RuntimeError("mongo down"),
    ):
        assert await service.list_medication_names_for_log(_log_for_names()) == []
# --- 關閉提醒要止住當日後續推播 -------------------------------------------
#
# 排程器的三個推播階段（T+0／T+20／T+30）都只查 medication_logs，條件是
# status="pending" 加上各自的已送出旗標，不會回頭確認那筆規則現在還開不開
# （見 MedicationLogRepository.list_pending_* 三個查詢）。所以把規則關掉只
# 影響「隔天還要不要展開」，當天已經展開的紀錄照樣會催促、照樣會發家屬逾時
# 警報。使用者的體感就是「我關了還是被催、家人還收到我漏吃藥的通知」。
#
# 修正的方向是在關閉的當下就把那些還沒確認的紀錄註銷，讓三個查詢自然濾掉
# 它們，而不是在排程器裡多做一次 reminder 的 join——推播路徑上的併發搶佔
# 行為已有既定保證，不動它。


def _reminder(enabled: bool = True) -> MedicationReminder:
    return MedicationReminder(
        _id="R123",
        creator_user_id="U_SELF",
        user_id="U_SELF",
        slot_type="morning",
        scheduled_time="08:00",
        enabled=enabled,
    )


class FakeReminderRepository:
    """`update_reminder` 用建構子注入的替身。

    openspec 的測試規則禁止用 monkey patch 換掉別處導入的實例，所以這裡走
    依賴注入，並記下 update_reminder 收到的 update_data，讓「enabled=False
    真的有送到資料層」這件事可以直接斷言。
    """

    def __init__(
        self,
        reminder: MedicationReminder | None,
        siblings: list[MedicationReminder] | None = None,
    ):
        self._reminder = reminder
        # 同一位使用者名下的其他提醒。改時段時要靠這份清單判斷目標時段是否
        # 已經有人佔著（「一個時段一份 document」的不變量，見
        # MedicationReminderRepository.find_or_create_reminder 的說明）；
        # create_reminders 的 409 檢查也是靠它判斷目標時段是否已有規則。
        self._siblings = siblings if siblings is not None else [reminder]
        self.received_update: dict | None = None
        # create_reminders 建立的每一筆規則，依呼叫順序累積——沒有 id 的
        # 規則比照 repository 真正建立時一定會有 id 的行為，指派一個假 id。
        self.created_reminders: list[MedicationReminder] = []

    async def get_reminder_by_id(self, reminder_id: str) -> MedicationReminder:
        return self._reminder

    async def list_reminders_by_user(self, user_id: str) -> list[MedicationReminder]:
        return [r for r in self._siblings if r and r.user_id == user_id]

    async def update_reminder(self, reminder_id: str, update_data: dict) -> MedicationReminder:
        self.received_update = dict(update_data)
        return self._reminder.model_copy(update=update_data)

    async def create_reminder(self, reminder: MedicationReminder) -> MedicationReminder:
        saved = reminder.model_copy(
            update={"id": reminder.id or f"R_NEW_{len(self.created_reminders)}"}
        )
        self.created_reminders.append(saved)
        return saved

    # delete_reminder 的替身：可設成刪除失敗，驗證失敗時不會順手註銷紀錄。
    delete_result: bool = True
    deleted_ids: list[str] | None = None

    async def delete_reminder(self, reminder_id: str) -> bool:
        if self.deleted_ids is None:
            self.deleted_ids = []
        self.deleted_ids.append(reminder_id)
        return self.delete_result


class FakeLogRepository:
    def __init__(
        self,
        cancelled: int = 0,
        resynced: tuple[int, int] = (0, 0),
        log: MedicationLog | None = None,
    ):
        self._cancelled = cancelled
        self._resynced = resynced
        # confirm_medication 用的單筆日誌替身：get_log_by_id 一律回傳它，
        # mark_as_taken／add_taken_medication 就地更新它並回傳新版本——與
        # FakeReminderRepository.get_reminder_by_id 同一種「忽略傳入的 id、
        # 只操作建構子給的那一筆」慣例，測試只涉及單一 log 的場景已足夠。
        self._log = log
        self.cancelled_reminder_ids: list[str] = []
        # 改排程走的是另一條路徑（對齊而非全部註銷），分開記錄才分得出服務層
        # 用的是哪一條——關閉是「這筆規則今天不算數了」，改排程是「今天改在
        # 另一個時刻」，兩者對當日紀錄的處置不同。
        self.resync_calls: list[dict] = []
        # 改排程到已經過去的時刻時，服務層會搶先寫一筆 cancelled 佔位，
        # 免得排程器展開出一筆假的漏服（見 _suppress_stale_new_slot）。
        self.upserted_logs: list[MedicationLog] = []
        self.mark_as_taken_calls: list[dict] = []
        self.add_taken_medication_calls: list[tuple[str, str]] = []

    async def cancel_pending_by_reminder(self, reminder_id: str) -> int:
        self.cancelled_reminder_ids.append(reminder_id)
        return self._cancelled

    async def resync_pending_by_reminder(
        self,
        reminder_id: str,
        scheduled_at,
        slot_type: str,
        urgent_at=None,
        timeout_at=None,
    ) -> tuple[int, int]:
        self.resync_calls.append(
            {
                "reminder_id": reminder_id,
                "scheduled_at": scheduled_at,
                "slot_type": slot_type,
                "urgent_at": urgent_at,
                "timeout_at": timeout_at,
            }
        )
        return self._resynced

    async def upsert_log(self, log: MedicationLog) -> tuple[MedicationLog, bool]:
        self.upserted_logs.append(log)
        return log, True

    async def get_log_by_id(self, log_id: str) -> MedicationLog | None:
        return self._log

    async def mark_as_taken(
        self,
        log_id: str,
        taken_at=None,
        taken_medication_ids: list[str] | None = None,
    ) -> MedicationLog | None:
        self.mark_as_taken_calls.append(
            {
                "log_id": log_id,
                "taken_medication_ids": list(taken_medication_ids)
                if taken_medication_ids
                else taken_medication_ids,
            }
        )
        if self._log is None:
            return None
        merged_ids = list(self._log.taken_medication_ids)
        for mid in taken_medication_ids or []:
            if mid not in merged_ids:
                merged_ids.append(mid)
        self._log = self._log.model_copy(
            update={
                "status": "taken",
                "taken_at": taken_at or FIXED_NOW,
                "taken_medication_ids": merged_ids,
            }
        )
        return self._log

    async def add_taken_medication(
        self, log_id: str, medication_id: str
    ) -> MedicationLog | None:
        self.add_taken_medication_calls.append((log_id, medication_id))
        if self._log is None:
            return None
        merged_ids = list(self._log.taken_medication_ids)
        if medication_id not in merged_ids:
            merged_ids.append(medication_id)
        self._log = self._log.model_copy(update={"taken_medication_ids": merged_ids})
        return self._log


# 改排程那段同時要算「今天是哪一天」與「離現在多久」。跟著真實時鐘跑的測試
# 會在午夜前後算出前一天的日期而飄紅，所以固定在一個沒有邊界問題的時刻上。
FIXED_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=TAIPEI_TZ)


def _service_with_fakes(
    reminder: MedicationReminder,
    cancelled: int = 0,
    siblings: list[MedicationReminder] | None = None,
    resynced: tuple[int, int] = (0, 0),
    now: datetime = FIXED_NOW,
):
    reminders = FakeReminderRepository(reminder, siblings=siblings)
    logs = FakeLogRepository(cancelled=cancelled, resynced=resynced)
    service = MedicationService(
        reminder_repository=reminders, log_repository=logs, clock=lambda: now
    )
    return service, reminders, logs


@pytest.mark.asyncio
async def test_disabling_reminder_cancels_pending_logs():
    service, reminders, logs = _service_with_fakes(_reminder(enabled=True), cancelled=1)

    result = await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(enabled=False),
    )

    # enabled=False 必須真的送到資料層：exclude_none 只濾掉 None，False 要留下。
    assert reminders.received_update["enabled"] is False
    assert result.enabled is False
    # 關閉的當下就把當日還沒確認的紀錄註銷，後續的催促與家屬警報才會停。
    assert logs.cancelled_reminder_ids == ["R123"]


@pytest.mark.asyncio
async def test_enabling_reminder_does_not_cancel_logs():
    """重新開啟不該註銷任何東西——那個時段當天可能已經有一筆正常在跑的紀錄。"""
    service, _, logs = _service_with_fakes(_reminder(enabled=False))

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(enabled=True),
    )

    assert logs.cancelled_reminder_ids == []


@pytest.mark.asyncio
async def test_changing_only_time_resyncs_instead_of_cancelling_everything():
    """只改時間的請求沒有帶 enabled（是 None），不能走「關閉」那條全部註銷的路。

    但當日已展開的紀錄仍停在舊時刻上，必須對齊——紀錄是展開當下的快照，三階
    推播只讀紀錄，不改的話 09:00 這筆規則今天仍會依 08:00 催促與發家屬警報。
    """
    service, reminders, logs = _service_with_fakes(_reminder(enabled=True))

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(scheduled_time="09:00"),
    )

    assert "enabled" not in reminders.received_update
    assert logs.cancelled_reminder_ids == []
    assert len(logs.resync_calls) == 1
    call = logs.resync_calls[0]
    assert call["reminder_id"] == "R123"
    assert call["slot_type"] == "morning"
    # 時刻要以台北時間的今天為基準，與排程器展開 scheduled_at 的算法一致，
    # 否則對不上已展開的那筆紀錄。
    assert call["scheduled_at"] == FIXED_NOW.replace(hour=9, minute=0)


@pytest.mark.asyncio
async def test_changing_slot_with_same_time_retags_instead_of_cancelling():
    """時刻沒變、只換時段名稱時，仍以對齊處理，不能把當日的紀錄註銷掉。

    使用者若自訂過時間（例如「早」07:15），把它改成「中」時該吃藥的那一刻並
    沒有變；註銷等於平白吃掉今天的提醒。repository 端據 scheduled_at 是否相同
    決定註銷或改標，服務層只負責把新排程交過去。
    """
    reminder = _reminder(enabled=True).model_copy(
        update={"slot_type": "morning", "scheduled_time": "07:15"}
    )
    service, _, logs = _service_with_fakes(reminder)

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(slot_type="noon"),
    )

    assert logs.cancelled_reminder_ids == []
    assert len(logs.resync_calls) == 1
    call = logs.resync_calls[0]
    assert call["slot_type"] == "noon"
    assert call["scheduled_at"] == FIXED_NOW.replace(hour=7, minute=15)


@pytest.mark.asyncio
async def test_updating_dates_only_does_not_touch_logs():
    """沒有動到排程的更新（例如只改結束日期）不該碰當日的紀錄。"""
    service, _, logs = _service_with_fakes(_reminder(enabled=True))

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(end_date=None),
    )

    assert logs.cancelled_reminder_ids == []
    assert logs.resync_calls == []


@pytest.mark.asyncio
async def test_resending_same_schedule_does_not_touch_logs():
    """把原值原樣重送一次不是改動，不該連帶動到當日的紀錄。"""
    reminder = _reminder(enabled=True)
    service, _, logs = _service_with_fakes(reminder)

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(
            slot_type=reminder.slot_type, scheduled_time=reminder.scheduled_time
        ),
    )

    assert logs.resync_calls == []


@pytest.mark.asyncio
async def test_disabling_and_changing_schedule_at_once_only_cancels():
    """同時關閉與改排程時只走關閉：全部註銷已涵蓋對齊要做的事，

    而且規則已經關了，排程器今天不會再為新時刻展開任何紀錄。
    """
    service, _, logs = _service_with_fakes(_reminder(enabled=True), cancelled=1)

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(enabled=False, scheduled_time="09:00"),
    )

    assert logs.cancelled_reminder_ids == ["R123"]
    assert logs.resync_calls == []


@pytest.mark.asyncio
async def test_changing_schedule_to_a_long_past_time_pre_cancels_that_slot():
    """改到今天已經過去太久的時刻時，先把該時刻註銷，今日不補提醒。

    排程器展開紀錄只看規則現在的 scheduled_time，不知道那個時刻是幾分鐘前才被
    改成這樣的。晚上八點把「晚 18:00」改成「早 08:00」，下一輪 tick 會為今天
    08:00 展開一筆紀錄，超過 misfire grace 便記成 missed——不推播，但會進「錯過
    時段的彙整通知」，家屬收到一則指向從未存在過的劑次的漏服通知。
    """
    long_past = FIXED_NOW - timedelta(minutes=90)
    service, _, logs = _service_with_fakes(_reminder(enabled=True))

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(
            scheduled_time=long_past.strftime("%H:%M")
        ),
    )

    assert len(logs.upserted_logs) == 1
    seeded = logs.upserted_logs[0]
    assert seeded.status == "cancelled"
    assert seeded.reminder_id == "R123"
    assert seeded.scheduled_at == long_past


@pytest.mark.asyncio
async def test_changing_schedule_to_a_just_passed_time_still_reminds_today():
    """剛過去幾分鐘（還在補推期限內）不預先註銷。

    使用者把時間往前挪一點，本來就可能是想現在被提醒；那則推播不該因為這道
    防線而消失。門檻是 misfire grace，不是「現在」。
    """
    just_passed = (FIXED_NOW - timedelta(minutes=5)).strftime("%H:%M")
    service, _, logs = _service_with_fakes(_reminder(enabled=True))

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(scheduled_time=just_passed),
    )

    assert logs.upserted_logs == []


@pytest.mark.asyncio
async def test_changing_schedule_to_a_future_time_does_not_pre_cancel():
    """改到今天還沒到的時刻不預先註銷——那一劑今天照常提醒。"""
    future = (FIXED_NOW + timedelta(minutes=90)).strftime("%H:%M")
    service, _, logs = _service_with_fakes(_reminder(enabled=True))

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(scheduled_time=future),
    )

    assert logs.upserted_logs == []


@pytest.mark.asyncio
async def test_disabled_reminder_does_not_get_a_pre_cancelled_log():
    """規則已停用時不寫佔位紀錄——排程器根本不會為它展開任何東西。"""
    long_past = (FIXED_NOW - timedelta(minutes=90)).strftime("%H:%M")
    service, _, logs = _service_with_fakes(_reminder(enabled=False))

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(scheduled_time=long_past),
    )

    assert logs.upserted_logs == []


@pytest.mark.asyncio
async def test_pre_cancel_failure_does_not_fail_the_update():
    """佔位紀錄寫不進去時，規則的更新仍然成功。

    這筆寫入是防禦性的記帳，不是使用者要求的那件事；讓它把一次成功的儲存變成
    錯誤，是拿一則可能的假漏服通知去換一個確定的失敗。
    """
    long_past = (FIXED_NOW - timedelta(minutes=90)).strftime("%H:%M")
    service, _, logs = _service_with_fakes(_reminder(enabled=True))
    logs.upsert_log = AsyncMock(side_effect=RuntimeError("mongo down"))

    result = await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(scheduled_time=long_past),
    )

    assert result.scheduled_time == long_past


@pytest.mark.asyncio
async def test_disable_by_patient_who_is_not_creator_also_cancels():
    """用藥者本人關閉自己的提醒同樣要止住推播——權限判斷放行 creator 或 user 兩者。"""
    reminder = _reminder(enabled=True).model_copy(
        update={"creator_user_id": "U_CARE", "user_id": "U_PATIENT"}
    )
    service, _, logs = _service_with_fakes(reminder, cancelled=1)

    await service.update_reminder(
        creator_user_id="U_PATIENT",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(enabled=False),
    )

    assert logs.cancelled_reminder_ids == ["R123"]


@pytest.mark.asyncio
async def test_disable_on_missing_reminder_does_not_cancel_logs():
    """提醒不存在時擋在 404，絕不能先把紀錄註銷掉。

    原本這條測的是「無權限的關閉請求擋在 403」。授權已移到 router（對象是
    提醒的**用藥者**，不再是建立者），無權的情境由
    tests/unit/routers/test_medications_authorization.py 覆蓋——那裡驗的是
    請求根本到不了服務層，因此更早也更完整。這裡保留同一個不變條件的另一面：
    任何提前結束的路徑都不得留下副作用。
    """
    service, _, logs = _service_with_fakes(None, cancelled=1)

    with pytest.raises(HTTPException) as excinfo:
        await service.update_reminder(
            creator_user_id="U_CARE",
            reminder_id="R404",
            request=UpdateMedicationReminderRequest(enabled=False),
        )

    assert excinfo.value.status_code == 404
    assert logs.cancelled_reminder_ids == []


@pytest.mark.asyncio
async def test_clearing_end_date_reaches_data_layer():
    """明確送 end_date=null 必須抵達資料層，這是把療程改回「長期」的唯一途徑。

    先前用 `exclude_none=True` 匯出請求，null 在服務層就被濾掉（資料層再濾
    一次），使用者一旦設過結束日期就永遠改不回長期——UI 只能反過來擋住這個
    操作。改用 `exclude_unset=True`：沒帶的欄位仍然不會出現在 update_data
    裡，「有帶且是 null」與「沒帶」從此是兩件不同的事。
    """
    reminder = _reminder().model_copy(update={"end_date": "2026-09-30"})
    service, reminders, _ = _service_with_fakes(reminder)

    result = await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(end_date=None),
    )

    assert "end_date" in reminders.received_update
    assert reminders.received_update["end_date"] is None
    assert result.end_date is None


@pytest.mark.asyncio
async def test_unset_fields_do_not_reach_data_layer():
    """`exclude_unset` 的另一半保證：沒帶的欄位不能被當成「清空」送下去。

    這條是上一個測試的反向護欄。若哪天有人把匯出改回 `model_dump()`（不帶
    任何 exclude），只改 enabled 的請求會連帶把 scheduled_time／start_date
    一起寫成 null，整筆提醒直接失效。
    """
    service, reminders, _ = _service_with_fakes(_reminder(enabled=True), cancelled=0)

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(enabled=False),
    )

    assert set(reminders.received_update) == {"enabled"}


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["scheduled_time", "start_date", "enabled"])
async def test_explicit_null_on_non_nullable_field_is_rejected(field: str):
    """只有 end_date 可以是 null。其餘欄位的 null 一律 400，不得寫進資料庫。

    `exclude_unset` 讓 null 得以通過服務層，代價是「明確送 null」對每個欄位
    都成立了。scheduled_time 被寫成 null 時排程器的 strptime 會拋錯並被
    except 吞掉——那筆提醒從此永遠不會觸發，且沒有任何錯誤回饋（見
    CreateMedicationReminderRequest._validate_slot_times 的同一個顧慮）。
    寧可在這裡擋成 400。
    """
    service, reminders, _ = _service_with_fakes(_reminder())

    with pytest.raises(HTTPException) as excinfo:
        await service.update_reminder(
            creator_user_id="U_SELF",
            reminder_id="R123",
            request=UpdateMedicationReminderRequest(**{field: None}),
        )

    assert excinfo.value.status_code == 400
    assert field in excinfo.value.detail
    # 擋下的請求不能留下任何副作用
    assert reminders.received_update is None


@pytest.mark.asyncio
async def test_changing_slot_type_to_free_slot_reaches_data_layer():
    """時段可以改：這是「時段唯讀但時間可改」造成的矛盾（早上 21:00）的解法。"""
    service, reminders, _ = _service_with_fakes(_reminder())

    result = await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(slot_type="evening"),
    )

    assert reminders.received_update["slot_type"] == "evening"
    assert result.slot_type == "evening"


@pytest.mark.asyncio
async def test_changing_slot_type_to_occupied_slot_is_rejected():
    """目標時段已經有另一筆提醒時必須擋成 409。

    「同一位使用者的同一個時段永遠只該有一份規則」是排程器不重複推播的前提
    （見 MedicationReminderRepository.find_or_create_reminder：`{user_id,
    slot_type}` 上刻意沒有 unique index，因為舊資料可能已有重複，建索引會讓
    應用起不來）。既然資料庫不擋，改時段這條新路徑就必須自己擋——否則使用者
    把早上改成晚上，晚上就有兩份規則，那個時段從此每天收到兩則推播。
    """
    occupied = MedicationReminder(
        _id="R456",
        creator_user_id="U_SELF",
        user_id="U_SELF",
        slot_type="evening",
        scheduled_time="18:00",
    )
    target = _reminder()
    service, reminders, _ = _service_with_fakes(target, siblings=[target, occupied])

    with pytest.raises(HTTPException) as excinfo:
        await service.update_reminder(
            creator_user_id="U_SELF",
            reminder_id="R123",
            request=UpdateMedicationReminderRequest(slot_type="evening"),
        )

    assert excinfo.value.status_code == 409
    assert reminders.received_update is None


@pytest.mark.asyncio
async def test_resending_same_slot_type_is_not_treated_as_conflict():
    """把時段送成它原本的值不是衝突——佔住那個時段的正是這筆提醒自己。"""
    target = _reminder()
    service, reminders, _ = _service_with_fakes(target, siblings=[target])

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(slot_type="morning", scheduled_time="07:30"),
    )

    assert reminders.received_update["scheduled_time"] == "07:30"


# --- 條目化：建立／更新提醒的藥品歸屬驗證與派生欄位 -------------------------


def _multi_entry_reminder(enabled: bool = True) -> MedicationReminder:
    """飯前 07:30（M1）、飯後 08:30（M2）兩個條目的規則。"""
    return MedicationReminder(
        _id="R123",
        creator_user_id="U_SELF",
        user_id="U_SELF",
        slot_type="morning",
        entries=[
            ReminderEntryInput(
                meal_timing="before_meal", scheduled_time="07:30", medication_ids=["M1"]
            ),
            ReminderEntryInput(
                meal_timing="after_meal", scheduled_time="08:30", medication_ids=["M2"]
            ),
        ],
        enabled=enabled,
    )


@pytest.mark.asyncio
async def test_update_reminder_rejects_scheduled_time_on_multi_entry_rule():
    """規則有多個條目時，一個 scheduled_time 不知道要對應哪一個時刻，
    必須改用 entries 整份更新（spec「提醒時間格式驗證」情境「多條目規則只
    改單一時間」）。"""
    service, reminders, _ = _service_with_fakes(_multi_entry_reminder())

    with pytest.raises(HTTPException) as excinfo:
        await service.update_reminder(
            creator_user_id="U_SELF",
            reminder_id="R123",
            request=UpdateMedicationReminderRequest(scheduled_time="09:00"),
        )

    assert excinfo.value.status_code == 400
    assert "詳細設定" in excinfo.value.detail
    assert reminders.received_update is None


@pytest.mark.asyncio
async def test_update_reminder_with_entries_derives_fields():
    """帶 entries 整份取代：派生欄位由新條目重算，且原始的 entries 輸入鍵
    被替換成 derive_entry_fields 展開後的四個欄位，兩者不會此後分岔。"""
    reminder = _multi_entry_reminder()
    fake_medications = FakeMedicationRepository(
        [
            Medication(id="M1", user_id="U_SELF", created_by_user_id="U_SELF", name="降血糖藥"),
            Medication(id="M3", user_id="U_SELF", created_by_user_id="U_SELF", name="新藥"),
        ]
    )
    reminders_repo = FakeReminderRepository(reminder)
    service = MedicationService(
        reminder_repository=reminders_repo,
        medication_repository=fake_medications,
        log_repository=FakeLogRepository(),
        clock=lambda: FIXED_NOW,
    )

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(
            entries=[
                ReminderEntryInput(
                    meal_timing="before_meal", scheduled_time="07:00", medication_ids=["M1"]
                ),
                ReminderEntryInput(
                    meal_timing="none", scheduled_time="09:00", medication_ids=["M3"]
                ),
            ]
        ),
    )

    received = reminders_repo.received_update
    assert received["scheduled_time"] == "07:00"
    assert received["timeout_anchor_time"] == "09:00"
    assert received["medication_ids"] == ["M1", "M3"]
    assert [e["meal_timing"] for e in received["entries"]] == ["before_meal", "none"]


@pytest.mark.asyncio
async def test_update_reminder_reassigning_medications_with_same_times_does_not_resync():
    """帶 entries 整份更新，但兩個條目的時刻都沒變、只是換了掛的藥品：
    `updated.slot_type`／`scheduled_time`／`timeout_anchor_time` 三個決定要不要
    對齊當日紀錄的欄位都跟改動前相同，不該觸發 `resync_pending_by_reminder`，
    更不該註銷——當日已展開的那筆紀錄該吃藥的時刻沒有變，動它就是平白吃掉
    使用者今天的提醒。這條純粹是釘住既有行為，不是新規則。"""
    reminder = _multi_entry_reminder()  # 飯前 07:30（M1）／飯後 08:30（M2）
    fake_medications = FakeMedicationRepository(
        [
            Medication(id="M3", user_id="U_SELF", created_by_user_id="U_SELF", name="新降血糖藥"),
            Medication(id="M4", user_id="U_SELF", created_by_user_id="U_SELF", name="新血壓藥"),
        ]
    )
    logs = FakeLogRepository()
    service = MedicationService(
        reminder_repository=FakeReminderRepository(reminder),
        medication_repository=fake_medications,
        log_repository=logs,
        clock=lambda: FIXED_NOW,
    )

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(
            entries=[
                ReminderEntryInput(
                    meal_timing="before_meal", scheduled_time="07:30", medication_ids=["M3"]
                ),
                ReminderEntryInput(
                    meal_timing="after_meal", scheduled_time="08:30", medication_ids=["M4"]
                ),
            ]
        ),
    )

    assert logs.resync_calls == []
    assert logs.cancelled_reminder_ids == []


@pytest.mark.asyncio
async def test_update_reminder_entries_rejects_medication_belonging_to_other_user():
    """條目掛的藥品若不屬於這筆提醒的用藥者，回 400，不寫入任何更新
    （spec「EntryInput.medication_ids 必須全部屬於該用藥者」）。"""
    reminder = _multi_entry_reminder()
    fake_medications = FakeMedicationRepository(
        [Medication(id="M9", user_id="U_OTHER", created_by_user_id="U_OTHER", name="別人的藥")]
    )
    reminders_repo = FakeReminderRepository(reminder)
    service = MedicationService(
        reminder_repository=reminders_repo,
        medication_repository=fake_medications,
        log_repository=FakeLogRepository(),
        clock=lambda: FIXED_NOW,
    )

    with pytest.raises(HTTPException) as excinfo:
        await service.update_reminder(
            creator_user_id="U_SELF",
            reminder_id="R123",
            request=UpdateMedicationReminderRequest(
                entries=[
                    ReminderEntryInput(
                        meal_timing="none", scheduled_time="08:00", medication_ids=["M9"]
                    )
                ]
            ),
        )

    assert excinfo.value.status_code == 400
    assert reminders_repo.received_update is None


@pytest.mark.asyncio
async def test_changing_only_timeout_anchor_time_resyncs_with_urgent_and_timeout():
    """只把飯後時間往後移：最早時刻（scheduled_time／T+0）不變，紀錄的
    `scheduled_at` 不用動，但最晚時刻變了，`urgent_at`／`timeout_at` 要跟著
    新的最晚時刻改寫（spec「只把飯後時間往後移」，design 決策 5 第二種情形）。
    """
    reminder = _multi_entry_reminder()  # 飯前 07:30／飯後 08:30
    fake_medications = FakeMedicationRepository(
        [
            Medication(id="M1", user_id="U_SELF", created_by_user_id="U_SELF", name="降血糖藥"),
            Medication(id="M2", user_id="U_SELF", created_by_user_id="U_SELF", name="血壓藥"),
        ]
    )
    reminders_repo = FakeReminderRepository(reminder)
    logs = FakeLogRepository()
    service = MedicationService(
        reminder_repository=reminders_repo,
        medication_repository=fake_medications,
        log_repository=logs,
        clock=lambda: FIXED_NOW,
    )

    await service.update_reminder(
        creator_user_id="U_SELF",
        reminder_id="R123",
        request=UpdateMedicationReminderRequest(
            entries=[
                ReminderEntryInput(
                    meal_timing="before_meal", scheduled_time="07:30", medication_ids=["M1"]
                ),
                ReminderEntryInput(
                    meal_timing="after_meal", scheduled_time="09:00", medication_ids=["M2"]
                ),
            ]
        ),
    )

    assert logs.cancelled_reminder_ids == []
    assert len(logs.resync_calls) == 1
    call = logs.resync_calls[0]
    # 最早時刻沒變，scheduled_at 停在原本的 07:30。
    assert call["scheduled_at"] == FIXED_NOW.replace(hour=7, minute=30)
    assert call["urgent_at"] == FIXED_NOW.replace(hour=9, minute=20)
    assert call["timeout_at"] == FIXED_NOW.replace(hour=9, minute=30)


# --- 逐藥確認 --------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_medication_per_drug_partial_confirmation_stays_pending():
    """逐藥確認未到齊時，紀錄維持原狀態，只累積這次按過的藥（spec「逐藥
    確認」情境「三種藥逐一確認」的前兩步）。"""
    log = MedicationLog(
        id="L123",
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-08-09T00:00:00Z",
        timeout_at="2026-08-09T00:30:00Z",
        status="pending",
    )
    reminder = MedicationReminder(
        _id="R123",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        medication_ids=["M1", "M2"],
    )
    fake_medications = FakeMedicationRepository(
        [_medication("M1", "脈優"), _medication("M2", "利尿劑")]
    )
    log_repo = FakeLogRepository(log=log)
    service = MedicationService(
        log_repository=log_repo,
        reminder_repository=FakeReminderRepository(reminder=reminder),
        medication_repository=fake_medications,
    )

    result = await service.confirm_medication(
        log_id="L123", user_id="U_PATIENT", medication_id="M1"
    )

    assert result.status == "pending"
    assert result.taken_medication_ids == ["M1"]
    assert log_repo.add_taken_medication_calls == [("L123", "M1")]
    # 未到齊不該連帶呼叫整批收尾。
    assert log_repo.mark_as_taken_calls == []


@pytest.mark.asyncio
async def test_confirm_medication_per_drug_completes_when_all_confirmed():
    """按下最後一顆藥的確認後，狀態收斂為 taken（spec「三種藥逐一確認」
    最後一步）。"""
    log = MedicationLog(
        id="L123",
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-08-09T00:00:00Z",
        timeout_at="2026-08-09T00:30:00Z",
        status="pending",
        taken_medication_ids=["M1"],
    )
    reminder = MedicationReminder(
        _id="R123",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        medication_ids=["M1", "M2"],
    )
    fake_medications = FakeMedicationRepository(
        [_medication("M1", "脈優"), _medication("M2", "利尿劑")]
    )
    log_repo = FakeLogRepository(log=log)
    service = MedicationService(
        log_repository=log_repo,
        reminder_repository=FakeReminderRepository(reminder=reminder),
        medication_repository=fake_medications,
    )

    result = await service.confirm_medication(
        log_id="L123", user_id="U_PATIENT", medication_id="M2"
    )

    assert result.status == "taken"
    assert set(result.taken_medication_ids) == {"M1", "M2"}
    assert len(log_repo.mark_as_taken_calls) == 1


@pytest.mark.asyncio
async def test_confirm_medication_disabled_drug_does_not_block_completion():
    """訊息送出後其中一種藥被停用，用藥者只按下仍有效的那顆：紀錄仍應轉
    `taken`（spec「訊息送出後藥品被停用」）。"""
    log = MedicationLog(
        id="L123",
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-08-09T00:00:00Z",
        timeout_at="2026-08-09T00:30:00Z",
        status="pending",
    )
    reminder = MedicationReminder(
        _id="R123",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        medication_ids=["M1", "M2"],
    )
    disabled_medication = _medication("M2", "利尿劑").model_copy(update={"enabled": False})
    fake_medications = FakeMedicationRepository([_medication("M1", "脈優"), disabled_medication])
    log_repo = FakeLogRepository(log=log)
    service = MedicationService(
        log_repository=log_repo,
        reminder_repository=FakeReminderRepository(reminder=reminder),
        medication_repository=fake_medications,
    )

    result = await service.confirm_medication(
        log_id="L123", user_id="U_PATIENT", medication_id="M1"
    )

    assert result.status == "taken"


# --- 藥品的列出與手動新增 ----------------------------------------------------


@pytest.mark.asyncio
async def test_list_medications_resolves_thumbnail_and_indication():
    fake_medications = FakeMedicationRepository(
        [
            Medication(
                id="M1",
                user_id="U_SELF",
                created_by_user_id="U_SELF",
                name="脈優錠",
                license_number="LIC-1",
            )
        ]
    )
    service = MedicationService(
        medication_repository=fake_medications,
        appearance_image_resolver=lambda lic: "https://example.com/x.jpg"
        if lic == "LIC-1"
        else None,
    )

    result = await service.list_medications("U_SELF")

    assert len(result) == 1
    assert result[0].thumbnail_url == "https://example.com/x.jpg"


@pytest.mark.asyncio
async def test_create_manual_medication_sets_manual_source_and_other_frequency():
    fake_medications = FakeMedicationRepository()
    service = MedicationService(medication_repository=fake_medications)
    req = CreateMedicationRequest(user_id="U_SELF", name="  維他命B群  ")

    created = await service.create_manual_medication("U_CARE", req)

    assert created.name == "維他命B群"
    assert created.source == "manual"
    assert created.frequency_code == "OTHER"
    assert created.created_by_user_id == "U_CARE"
    assert fake_medications.created_medications == [created]


class _FakeCatalog:
    """藥證庫替身：藥名 → 比對結果（唯一命中帶證號、多候選證號為 None、查無 None）。"""

    class _Match:
        def __init__(self, license_number):
            self.license_number = license_number

    def __init__(self, by_name: dict):
        self._by_name = by_name

    def match(self, name: str):
        return self._by_name.get(name)


class _RecordingOtcAlert:
    def __init__(self):
        self.calls: list[tuple[str, list[str]]] = []

    async def check(self, patient_user_id: str, medication_ids):
        self.calls.append((patient_user_id, list(medication_ids)))


@pytest.mark.asyncio
async def test_create_manual_medication_pins_unique_catalog_hit_and_runs_interaction_check():
    """手動新增的藥要能被相衝偵測看見：唯一命中釘證號，新增後排一次偵測。

    之前手動新增永遠沒有證號，OtcAlertService 拿不到成分與 ATC，長輩自己
    輸入的「普拿疼」在四條規則裡都是隱形的，也沒有任何地方觸發偵測。
    """
    fake_medications = FakeMedicationRepository()
    alert = _RecordingOtcAlert()
    service = MedicationService(
        medication_repository=fake_medications,
        catalog_service=_FakeCatalog({"普拿疼加強錠": _FakeCatalog._Match("衛署藥輸字第023623號")}),
        otc_alert_service=alert,
    )

    created = await service.create_manual_medication(
        "U_CARE", CreateMedicationRequest(user_id="U_ELDER", name="普拿疼加強錠")
    )
    await asyncio.gather(*service._otc_alert_tasks)

    assert created.license_number == "衛署藥輸字第023623號"
    assert created.source == "manual"
    # 偵測對象是服藥的人（家屬代為新增時是長輩），不是新增者
    assert alert.calls == [("U_ELDER", [created.id])]


@pytest.mark.asyncio
async def test_create_manual_medication_leaves_license_empty_on_ambiguous_or_missing_name():
    """多張候選或查無都不釘證號——挑一張就是編造，與藥袋掃描同一條規則。
    偵測照樣排一次：中藥走的是方名比對，不需要證號。"""
    fake_medications = FakeMedicationRepository()
    alert = _RecordingOtcAlert()
    service = MedicationService(
        medication_repository=fake_medications,
        catalog_service=_FakeCatalog({"感冒液": _FakeCatalog._Match(None)}),
        otc_alert_service=alert,
    )

    ambiguous = await service.create_manual_medication(
        "U_SELF", CreateMedicationRequest(user_id="U_SELF", name="感冒液")
    )
    unknown = await service.create_manual_medication(
        "U_SELF", CreateMedicationRequest(user_id="U_SELF", name="阿嬤的藥")
    )
    await asyncio.gather(*service._otc_alert_tasks)

    assert ambiguous.license_number is None
    assert unknown.license_number is None
    assert [ids for _, ids in alert.calls] == [[ambiguous.id], [unknown.id]]


@pytest.mark.asyncio
async def test_create_manual_medication_survives_catalog_and_alert_failures():
    """藥證庫或偵測服務出錯都不能讓「存一個藥名」失敗。"""

    class _Exploding:
        def match(self, name):
            raise RuntimeError("catalog down")

        async def check(self, patient_user_id, medication_ids):
            raise RuntimeError("alert down")

    fake_medications = FakeMedicationRepository()
    service = MedicationService(
        medication_repository=fake_medications,
        catalog_service=_Exploding(),
        otc_alert_service=_Exploding(),
    )

    created = await service.create_manual_medication(
        "U_SELF", CreateMedicationRequest(user_id="U_SELF", name="普拿疼")
    )
    await asyncio.gather(*service._otc_alert_tasks)  # 不得拋出

    assert created.license_number is None
    assert fake_medications.created_medications == [created]


# --- 推播分區資料與已服用藥名 ------------------------------------------------


@pytest.mark.asyncio
async def test_medication_groups_for_log_orders_by_meal_timing_and_excludes_taken():
    log = MedicationLog(
        id="L123",
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-08-09T00:00:00Z",
        timeout_at="2026-08-09T00:30:00Z",
        status="pending",
        taken_medication_ids=["M1"],
    )
    reminder = MedicationReminder(
        _id="R123",
        creator_user_id="U_CARE",
        user_id="U_PATIENT",
        slot_type="morning",
        entries=[
            ReminderEntryInput(
                meal_timing="before_meal", scheduled_time="07:30", medication_ids=["M1"]
            ),
            ReminderEntryInput(
                meal_timing="after_meal", scheduled_time="08:30", medication_ids=["M2", "M3"]
            ),
        ],
    )
    fake_medications = FakeMedicationRepository(
        [_medication("M1", "降血糖藥"), _medication("M2", "血壓藥"), _medication("M3", "胃藥")]
    )
    service = MedicationService(
        reminder_repository=FakeReminderRepository(reminder=reminder),
        medication_repository=fake_medications,
    )

    groups = await service.medication_groups_for_log(log)

    # M1 已在 taken_medication_ids 裡，飯前那組全部確認完畢，不該再出現。
    assert [g.meal_timing for g in groups] == ["after_meal"]
    assert [mid for mid, _ in groups[0].items] == ["M2", "M3"]


@pytest.mark.asyncio
async def test_taken_names_for_log_preserves_order_and_ignores_disabled():
    """已確認藥品的藥名依 taken_medication_ids 的順序，且不做有效性篩選——
    藥品之後被停用不影響「那次吃了什麼」的歷史顯示。"""
    log = MedicationLog(
        id="L123",
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-08-09T00:00:00Z",
        timeout_at="2026-08-09T00:30:00Z",
        status="taken",
        taken_medication_ids=["M2", "M1"],
    )
    disabled_m1 = _medication("M1", "脈優").model_copy(update={"enabled": False})
    fake_medications = FakeMedicationRepository([_medication("M2", "利尿劑"), disabled_m1])
    service = MedicationService(medication_repository=fake_medications)

    names = await service.taken_names_for_log(log)

    assert names == ["利尿劑", "脈優"]


# ── 刪除提醒：與關閉一樣要註銷當日還沒確認的紀錄 ───────────────────────


@pytest.mark.asyncio
async def test_deleting_reminder_cancels_pending_logs():
    """刪掉規則之後，當天已展開、還沒確認的那筆仍會走完 T+20 催促與 T+30 家屬
    逾時警報（三個階段只查紀錄、不回頭確認規則還在不在）。刪除必須與關閉做同一
    件事：把紀錄註銷，後續推播才會停。"""
    service, reminders, logs = _service_with_fakes(_reminder(enabled=True), cancelled=1)

    ok = await service.delete_reminder(creator_user_id="U_SELF", reminder_id="R123")

    assert ok is True
    assert reminders.deleted_ids == ["R123"]
    assert logs.cancelled_reminder_ids == ["R123"]


@pytest.mark.asyncio
async def test_failed_delete_does_not_cancel_logs():
    """刪除失敗代表規則還在，當天的紀錄該留著。"""
    service, reminders, logs = _service_with_fakes(_reminder(enabled=True), cancelled=1)
    reminders.delete_result = False

    ok = await service.delete_reminder(creator_user_id="U_SELF", reminder_id="R123")

    assert ok is False
    assert logs.cancelled_reminder_ids == []


@pytest.mark.asyncio
async def test_delete_unknown_reminder_is_404_and_touches_nothing():
    service, reminders, logs = _service_with_fakes(None)

    with pytest.raises(HTTPException) as exc_info:
        await service.delete_reminder(creator_user_id="U_SELF", reminder_id="R_MISSING")

    assert exc_info.value.status_code == 404
    assert reminders.deleted_ids is None
    assert logs.cancelled_reminder_ids == []
