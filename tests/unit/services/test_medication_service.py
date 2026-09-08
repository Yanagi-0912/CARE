from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import HTTPException

from app.models.family_tree import FamilyMember, FamilyTree
from app.models.medication import (
    TAIPEI_TZ,
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
    # 族譜檢查已移出服務層：為他人建立提醒的授權由 router 經
    # FamilyAuthorizationService 判定（GENERAL 寫入權），拒絕的情境由
    # tests/unit/routers/test_medications_authorization.py 覆蓋。
    with patch(
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
        reminder: MedicationReminder,
        siblings: list[MedicationReminder] | None = None,
    ):
        self._reminder = reminder
        # 同一位使用者名下的其他提醒。改時段時要靠這份清單判斷目標時段是否
        # 已經有人佔著（「一個時段一份 document」的不變量，見
        # MedicationReminderRepository.find_or_create_reminder 的說明）。
        self._siblings = siblings if siblings is not None else [reminder]
        self.received_update: dict | None = None

    async def get_reminder_by_id(self, reminder_id: str) -> MedicationReminder:
        return self._reminder

    async def list_reminders_by_user(self, user_id: str) -> list[MedicationReminder]:
        return [r for r in self._siblings if r.user_id == user_id]

    async def update_reminder(self, reminder_id: str, update_data: dict) -> MedicationReminder:
        self.received_update = dict(update_data)
        return self._reminder.model_copy(update=update_data)


class FakeLogRepository:
    def __init__(self, cancelled: int = 0, resynced: tuple[int, int] = (0, 0)):
        self._cancelled = cancelled
        self._resynced = resynced
        self.cancelled_reminder_ids: list[str] = []
        # 改排程走的是另一條路徑（對齊而非全部註銷），分開記錄才分得出服務層
        # 用的是哪一條——關閉是「這筆規則今天不算數了」，改排程是「今天改在
        # 另一個時刻」，兩者對當日紀錄的處置不同。
        self.resync_calls: list[dict] = []
        # 改排程到已經過去的時刻時，服務層會搶先寫一筆 cancelled 佔位，
        # 免得排程器展開出一筆假的漏服（見 _suppress_stale_new_slot）。
        self.upserted_logs: list[MedicationLog] = []

    async def cancel_pending_by_reminder(self, reminder_id: str) -> int:
        self.cancelled_reminder_ids.append(reminder_id)
        return self._cancelled

    async def resync_pending_by_reminder(
        self, reminder_id: str, scheduled_at, slot_type: str
    ) -> tuple[int, int]:
        self.resync_calls.append(
            {
                "reminder_id": reminder_id,
                "scheduled_at": scheduled_at,
                "slot_type": slot_type,
            }
        )
        return self._resynced

    async def upsert_log(self, log: MedicationLog) -> tuple[MedicationLog, bool]:
        self.upserted_logs.append(log)
        return log, True


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
