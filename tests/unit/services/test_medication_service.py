from unittest.mock import AsyncMock, patch
import pytest
from fastapi import HTTPException

from app.models.family_tree import FamilyMember, FamilyTree
from app.models.medication import (
    CreateMedicationReminderRequest,
    Medication,
    MedicationLog,
    MedicationReminder,
    UpdateMedicationReminderRequest,
)
from app.services.medication.medication_service import MedicationService


class FakeMedicationRepository:
    """`get_user_reminders_with_medications` 用建構子注入的替身——不必碰
    MongoDB，也不需要 monkeypatch 掉整個 MedicationRepository。"""

    def __init__(self, medications: list[Medication] | None = None):
        self._medications = medications or []
        self.queried_ids: list[str] | None = None

    async def find_by_ids(self, medication_ids: list[str]) -> list[Medication]:
        self.queried_ids = list(medication_ids)
        return [m for m in self._medications if m.id in medication_ids]


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
async def test_create_reminders_for_self(medication_service):
    req = CreateMedicationReminderRequest(
        user_id="U_SELF",
        slots=["morning", "evening"],
        start_date="2026-07-26",
    )
    with patch(
        "app.services.medication.medication_service.MedicationReminderRepository.create_reminder",
        new_callable=AsyncMock,
    ) as mock_create:
        mock_create.side_effect = lambda r: r
        reminders = await medication_service.create_reminders(
            creator_user_id="U_SELF", request=req
        )

        assert len(reminders) == 2
        assert reminders[0].slot_type == "morning"
        assert reminders[0].scheduled_time == "08:00"
        assert reminders[1].slot_type == "evening"
        assert reminders[1].scheduled_time == "18:00"


@pytest.mark.asyncio
async def test_create_reminders_for_family_member(medication_service):
    fake_tree = FamilyTree(
        user_id="U_CARE",
        family_members=[FamilyMember(user_id="U_MEMBER", is_care_recipient=True)],
        created_at="2026-07-26T00:00:00Z",
        updated_at="2026-07-26T00:00:00Z",
    )
    req = CreateMedicationReminderRequest(
        user_id="U_MEMBER",
        slots=["noon"],
        start_date="2026-07-26",
    )
    with patch(
        "app.services.medication.medication_service.FamilyTreeRepository.get_by_user_id",
        new_callable=AsyncMock,
        return_value=fake_tree,
    ), patch(
        "app.services.medication.medication_service.MedicationReminderRepository.create_reminder",
        new_callable=AsyncMock,
        side_effect=lambda r: r,
    ):
        reminders = await medication_service.create_reminders(
            creator_user_id="U_CARE", request=req
        )
        assert len(reminders) == 1
        assert reminders[0].user_id == "U_MEMBER"
        assert reminders[0].slot_type == "noon"


@pytest.mark.asyncio
async def test_create_reminders_rejects_non_family_member(medication_service):
    fake_tree = FamilyTree(
        user_id="U_CARE",
        family_members=[],
        created_at="2026-07-26T00:00:00Z",
        updated_at="2026-07-26T00:00:00Z",
    )
    req = CreateMedicationReminderRequest(
        user_id="U_STRANGER",
        slots=["morning"],
    )
    with patch(
        "app.services.medication.medication_service.FamilyTreeRepository.get_by_user_id",
        new_callable=AsyncMock,
        return_value=fake_tree,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await medication_service.create_reminders(
                creator_user_id="U_CARE", request=req
            )
        assert exc_info.value.status_code == 400
        assert "用藥對象必須是您的家庭成員" in exc_info.value.detail


@pytest.mark.asyncio
async def test_confirm_medication_success(medication_service):
    fake_log = MedicationLog(
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-07-26T08:00:00Z",
        timeout_at="2026-07-26T08:30:00Z",
        status="pending",
    )
    taken_log = fake_log.model_copy(update={"status": "taken"})
    with patch(
        "app.services.medication.medication_service.MedicationLogRepository.get_log_by_id",
        new_callable=AsyncMock,
        return_value=fake_log,
    ), patch(
        "app.services.medication.medication_service.MedicationLogRepository.mark_as_taken",
        new_callable=AsyncMock,
        return_value=taken_log,
    ):
        res = await medication_service.confirm_medication(
            log_id="L123", user_id="U_PATIENT"
        )
        assert res.status == "taken"


@pytest.mark.asyncio
async def test_confirm_medication_forbidden_for_other_user(medication_service):
    fake_log = MedicationLog(
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-07-26T08:00:00Z",
        timeout_at="2026-07-26T08:30:00Z",
    )
    with patch(
        "app.services.medication.medication_service.MedicationLogRepository.get_log_by_id",
        new_callable=AsyncMock,
        return_value=fake_log,
    ):
        with pytest.raises(HTTPException) as excinfo:
            await medication_service.confirm_medication(
                log_id="L123", user_id="U_OTHER"
            )
        assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_confirm_medication_missed_status_update(medication_service):
    missed_log = MedicationLog(
        reminder_id="R123",
        user_id="U_PATIENT",
        alert_notify_user_id="U_CARE",
        slot_type="morning",
        scheduled_at="2026-07-26T08:00:00Z",
        timeout_at="2026-07-26T08:30:00Z",
        status="missed",
    )
    taken_log = missed_log.model_copy(update={"status": "taken"})
    with patch(
        "app.services.medication.medication_service.MedicationLogRepository.get_log_by_id",
        new_callable=AsyncMock,
        return_value=missed_log,
    ), patch(
        "app.services.medication.medication_service.MedicationLogRepository.mark_as_taken",
        new_callable=AsyncMock,
        return_value=taken_log,
    ):
        res = await medication_service.confirm_medication(
            log_id="L123", user_id="U_PATIENT"
        )
        assert res.status == "taken"


@pytest.mark.asyncio
async def test_create_reminders_custom_slot_times(medication_service):
    req = CreateMedicationReminderRequest(
        user_id="U_SELF",
        slots=["morning", "evening"],
        slot_times={"morning": "07:30", "evening": "19:00"},
        start_date="2026-07-29",
    )
    with patch(
        "app.services.medication.medication_service.MedicationReminderRepository.create_reminder",
        new_callable=AsyncMock,
        side_effect=lambda r: r,
    ):
        reminders = await medication_service.create_reminders(
            creator_user_id="U_SELF", request=req
        )

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
    with patch(
        "app.services.medication.medication_service.FamilyTreeRepository.get_by_user_id",
        new_callable=AsyncMock,
        return_value=fake_tree,
    ), patch(
        "app.services.medication.medication_service.MedicationReminderRepository.list_reminders_by_user",
        new_callable=AsyncMock,
        return_value=[],
    ) as mock_list:
        # Allowed for family member
        reminders = await medication_service.get_user_reminders("U_MEMBER", requester_user_id="U_CARE")
        assert reminders == []
        mock_list.assert_awaited_once_with("U_MEMBER")

        # Rejected for non-family member
        with pytest.raises(HTTPException) as exc_info:
            await medication_service.get_user_reminders("U_STRANGER", requester_user_id="U_CARE")
        assert exc_info.value.status_code == 400


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
