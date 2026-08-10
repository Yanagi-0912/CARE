from datetime import datetime, timedelta, timezone

import pytest

from app.models.family_tree import FamilyMember, FamilyTree
from app.models.medication import MedicationReminder
from app.models.prescription import (
    CommitDrugItem,
    CommitPrescriptionDraftRequest,
    PrescriptionDraft,
    RecognitionResult,
    RecognizedDrug,
)
from app.services.medication.drug_catalog_service import DrugCatalogMatch
from app.services.medication.prescription_scan_service import (
    DraftExpiredError,
    DraftNotFoundError,
    PrescriptionScanService,
    SlotsRequiredError,
    TargetNotInFamilyError,
)


class FakeOcr:
    def __init__(self, result: RecognitionResult):
        self._result = result

    async def recognize(self, image_bytes: bytes, mime_type: str) -> RecognitionResult:
        return self._result


class FakeCatalog:
    def __init__(self, matches: dict[str, DrugCatalogMatch] | None = None):
        self._matches = matches or {}

    def match(self, name: str):
        return self._matches.get(name)


class FakeDraftRepository:
    def __init__(self):
        self.saved: list[PrescriptionDraft] = []
        self.draft: PrescriptionDraft | None = None
        self.commit_result: tuple[bool, list[str]] | None = None
        self.commit_calls: list[tuple] = []
        self.release_calls: list[tuple] = []
        # 模擬真正儲存端「committed_medication_ids 是不是我方才寫入的那組」
        # 這個條件式狀態，好讓「寫入失敗 → 釋放 → 重試真的能重新取得」這種
        # 場景可以被完整地測試到，而不只是驗證呼叫過 release_commit。
        self._committed_medication_ids: list[str] | None = None

    async def create(self, draft: PrescriptionDraft):
        self.saved.append(draft)
        return draft

    async def find_by_id_for_user(self, draft_id: str, user_id: str):
        if self.draft is None:
            return None
        if self.draft.draft_id != draft_id or self.draft.creator_user_id != user_id:
            return None
        return self.draft

    async def mark_committed(self, draft_id, user_id, medication_ids):
        self.commit_calls.append((draft_id, user_id, list(medication_ids)))
        if self.commit_result is not None:
            return self.commit_result
        if self._committed_medication_ids is not None:
            return False, list(self._committed_medication_ids)
        self._committed_medication_ids = list(medication_ids)
        return True, list(medication_ids)

    async def release_commit(self, draft_id, user_id, medication_ids):
        self.release_calls.append((draft_id, user_id, list(medication_ids)))
        # 只釋放「呼叫端自己方才取得的那組 id」，呼應真正 repository 的條件式更新。
        if self._committed_medication_ids == list(medication_ids):
            self._committed_medication_ids = None
            return True
        return False


class FakeMedicationRepository:
    def __init__(self, fail_times: int = 0):
        self.created = []
        # 讓測試能模擬「取得提交權之後、寫入時發生暫時性資料庫錯誤」——
        # 呼叫次數在門檻內就拋錯，之後恢復正常，藉此驗證重試真的能成功。
        self._fail_times = fail_times
        self.attempts = 0

    async def create_many(self, medications):
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise RuntimeError("暫時性資料庫錯誤")
        self.created.extend(medications)
        return medications


class FakeReminderRepository:
    def __init__(self, existing=None, reactivate_slots=None):
        self.existing = existing or []
        # 模擬「命中的既有規則原本不可排程，這次呼叫把它改回可排程」——
        # 只在第一次命中該時段時回報 True，之後同一次提交內再碰到同一個
        # 時段（例如 TID 藥的三個時段裡有兩顆藥都連到同一個時段）就已經
        # 是活的了，對應真正 repository「命中活著的規則就不再寫入」的行為。
        self.reactivate_slots = set(reactivate_slots or [])
        self.created = []
        self.links: list[tuple[str, list[str]]] = []
        self.find_or_create_calls: list[tuple] = []

    async def find_or_create_reminder(
        self, user_id, slot_type, creator_user_id, scheduled_time
    ):
        # 以 (user_id, slot_type) 當鍵模擬真正 repository 的原子 upsert：
        # 命中既有規則就原樣回傳，不新建、不覆寫；沒有才建立一筆新的。
        self.find_or_create_calls.append(
            (user_id, slot_type, creator_user_id, scheduled_time)
        )
        for reminder in self.existing:
            if reminder.user_id == user_id and reminder.slot_type == slot_type:
                reactivated = slot_type in self.reactivate_slots
                self.reactivate_slots.discard(slot_type)
                return reminder, reactivated
        reminder = MedicationReminder(
            id=f"R_{slot_type}",
            creator_user_id=creator_user_id,
            user_id=user_id,
            slot_type=slot_type,
            scheduled_time=scheduled_time,
        )
        self.created.append(reminder)
        self.existing.append(reminder)
        return reminder, False

    async def link_medications_to_reminder(self, reminder_id, medication_ids):
        self.links.append((reminder_id, list(medication_ids)))
        return True


class FakeFamilyTreeRepository:
    def __init__(self, tree: FamilyTree | None = None):
        self._tree = tree

    async def get_by_user_id(self, user_id: str):
        return self._tree


def _tree(*members: FamilyMember) -> FamilyTree:
    now = datetime.now(timezone.utc)
    return FamilyTree(
        user_id="U_FAMILY",
        family_members=list(members),
        created_at=now,
        updated_at=now,
    )


def _service(
    ocr=None,
    catalog=None,
    drafts=None,
    medications=None,
    reminders=None,
    family=None,
):
    return PrescriptionScanService(
        ocr_service=ocr or FakeOcr(RecognitionResult()),
        catalog_service=catalog or FakeCatalog(),
        draft_repository=drafts or FakeDraftRepository(),
        medication_repository=medications or FakeMedicationRepository(),
        reminder_repository=reminders or FakeReminderRepository(),
        family_tree_repository=family or FakeFamilyTreeRepository(),
        ttl_minutes=60,
    )


def _recognition(*drugs: RecognizedDrug, patient_name="王大明") -> RecognitionResult:
    return RecognitionResult(
        institution="臺大醫院",
        patient_name=patient_name,
        dispensed_date="2026-08-09",
        drugs=list(drugs),
    )


def _match(name="脈優錠5毫克") -> DrugCatalogMatch:
    return DrugCatalogMatch(
        license_number="衛署藥製字第000001號",
        name_zh=name,
        name_en="AMLODIPINE TABLETS 5MG",
        score=1.0,
    )


# --- scan ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_marks_high_confidence_when_everything_checks_out():
    drug = RecognizedDrug(name="脈優錠5毫克", frequency_code="TID")
    drafts = FakeDraftRepository()
    service = _service(
        ocr=FakeOcr(_recognition(drug)),
        catalog=FakeCatalog({"脈優錠5毫克": _match()}),
        drafts=drafts,
        family=FakeFamilyTreeRepository(
            _tree(FamilyMember(user_id="U_PATIENT", display_name="王大明"))
        ),
    )

    draft = await service.scan(b"image", "image/jpeg", "U_FAMILY")

    assert draft.confidence_level == "high"
    assert drafts.saved == [draft]


@pytest.mark.asyncio
async def test_scan_falls_to_medium_when_a_drug_name_is_not_in_the_catalog():
    """藥名比對不到就不給一鍵確認——這是偵測模型錯讀的唯一手段。"""
    drugs = [
        RecognizedDrug(name="脈優錠5毫克", frequency_code="TID"),
        RecognizedDrug(name="讀錯的藥名", frequency_code="BID"),
    ]
    service = _service(
        ocr=FakeOcr(_recognition(*drugs)),
        catalog=FakeCatalog({"脈優錠5毫克": _match()}),
        family=FakeFamilyTreeRepository(
            _tree(FamilyMember(user_id="U_PATIENT", display_name="王大明"))
        ),
    )

    draft = await service.scan(b"image", "image/jpeg", "U_FAMILY")

    assert draft.confidence_level == "medium"


@pytest.mark.asyncio
async def test_scan_falls_to_medium_when_frequency_is_unclassified():
    drug = RecognizedDrug(name="脈優錠5毫克", frequency_code="OTHER")
    service = _service(
        ocr=FakeOcr(_recognition(drug)),
        catalog=FakeCatalog({"脈優錠5毫克": _match()}),
        family=FakeFamilyTreeRepository(
            _tree(FamilyMember(user_id="U_PATIENT", display_name="王大明"))
        ),
    )

    draft = await service.scan(b"image", "image/jpeg", "U_FAMILY")

    assert draft.confidence_level == "medium"


@pytest.mark.asyncio
async def test_scan_falls_to_medium_when_no_family_member_matches_the_patient_name():
    drug = RecognizedDrug(name="脈優錠5毫克", frequency_code="TID")
    service = _service(
        ocr=FakeOcr(_recognition(drug)),
        catalog=FakeCatalog({"脈優錠5毫克": _match()}),
        family=FakeFamilyTreeRepository(_tree()),
    )

    draft = await service.scan(b"image", "image/jpeg", "U_FAMILY")

    assert draft.confidence_level == "medium"
    assert draft.suggested_user_id is None


@pytest.mark.asyncio
async def test_scan_fills_licence_and_raises_confidence_on_catalog_hit():
    drug = RecognizedDrug(name="脈優錠5毫克", frequency_code="TID")
    service = _service(
        ocr=FakeOcr(_recognition(drug)),
        catalog=FakeCatalog({"脈優錠5毫克": _match()}),
    )

    draft = await service.scan(b"image", "image/jpeg", "U_FAMILY")

    assert draft.recognition.drugs[0].license_number == "衛署藥製字第000001號"
    assert draft.recognition.drugs[0].name_confidence == "high"


@pytest.mark.asyncio
async def test_scan_keeps_unmatched_drug_at_low_confidence():
    drug = RecognizedDrug(name="讀錯的藥名", frequency_code="TID")
    service = _service(ocr=FakeOcr(_recognition(drug)), catalog=FakeCatalog())

    draft = await service.scan(b"image", "image/jpeg", "U_FAMILY")

    assert draft.recognition.drugs[0].name_confidence == "low"
    assert draft.recognition.drugs[0].license_number is None


@pytest.mark.asyncio
async def test_scan_suggests_the_family_member_whose_name_matches():
    drug = RecognizedDrug(name="某藥", frequency_code="TID")
    service = _service(
        ocr=FakeOcr(_recognition(drug, patient_name="王大明")),
        family=FakeFamilyTreeRepository(
            _tree(
                FamilyMember(user_id="U_OTHER", display_name="李小華"),
                FamilyMember(user_id="U_PATIENT", display_name="王大明"),
            )
        ),
    )

    draft = await service.scan(b"image", "image/jpeg", "U_FAMILY")

    assert draft.suggested_user_id == "U_PATIENT"


@pytest.mark.asyncio
async def test_scan_sets_expiry_from_ttl():
    service = _service(
        ocr=FakeOcr(_recognition(RecognizedDrug(name="某藥", frequency_code="QD")))
    )

    before = datetime.now(timezone.utc)
    draft = await service.scan(b"image", "image/jpeg", "U_FAMILY")

    assert draft.expires_at > before + timedelta(minutes=59)
    assert draft.expires_at < before + timedelta(minutes=61)


def _stored_draft(*drugs: RecognizedDrug, expires_in_minutes=60) -> PrescriptionDraft:
    return PrescriptionDraft(
        draft_id="D1",
        creator_user_id="U_FAMILY",
        recognition=_recognition(*drugs),
        confidence_level="high",
        expires_at=datetime.now(timezone.utc)
        + timedelta(minutes=expires_in_minutes),
    )


def _request(*items: CommitDrugItem, user_id="U_PATIENT"):
    return CommitPrescriptionDraftRequest(user_id=user_id, drugs=list(items))


# --- get_draft -------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_draft_returns_the_stored_draft():
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥", frequency_code="QD"))
    service = _service(drafts=drafts)

    draft = await service.get_draft("D1", "U_FAMILY")

    assert draft.draft_id == "D1"


@pytest.mark.asyncio
async def test_get_draft_raises_not_found_for_a_missing_draft():
    service = _service()

    with pytest.raises(DraftNotFoundError):
        await service.get_draft("D_MISSING", "U_FAMILY")


@pytest.mark.asyncio
async def test_get_draft_raises_not_found_for_another_users_draft():
    """他人的 draft_id：找不到與不屬於自己統一回同一種例外，不能讓呼叫端
    藉由回應差異探測出這個 draft_id 是否存在。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥", frequency_code="QD"))
    service = _service(drafts=drafts)

    with pytest.raises(DraftNotFoundError):
        await service.get_draft("D1", "U_STRANGER")


# --- commit --------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_rejects_unknown_draft():
    service = _service()

    with pytest.raises(DraftNotFoundError):
        await service.commit(
            "D1", "U_FAMILY", _request(CommitDrugItem(name="某藥", frequency_code="QD"))
        )


@pytest.mark.asyncio
async def test_commit_rejects_expired_draft():
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(
        RecognizedDrug(name="某藥"), expires_in_minutes=-1
    )
    service = _service(drafts=drafts)

    with pytest.raises(DraftExpiredError):
        await service.commit(
            "D1", "U_FAMILY", _request(CommitDrugItem(name="某藥", frequency_code="QD"))
        )


@pytest.mark.asyncio
async def test_commit_rejects_target_outside_the_family_tree():
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    medications = FakeMedicationRepository()
    service = _service(
        drafts=drafts,
        medications=medications,
        family=FakeFamilyTreeRepository(_tree()),
    )

    with pytest.raises(TargetNotInFamilyError):
        await service.commit(
            "D1", "U_FAMILY", _request(CommitDrugItem(name="某藥", frequency_code="QD"))
        )

    assert medications.created == []


@pytest.mark.asyncio
async def test_commit_allows_creating_for_self_without_a_family_tree():
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    service = _service(drafts=drafts, family=FakeFamilyTreeRepository(None))

    result = await service.commit(
        "D1",
        "U_FAMILY",
        _request(
            CommitDrugItem(name="某藥", frequency_code="QD"), user_id="U_FAMILY"
        ),
    )

    assert len(result.medication_ids) == 1


@pytest.mark.asyncio
async def test_commit_links_tid_drug_to_three_slots():
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1", "U_FAMILY", _request(CommitDrugItem(name="某藥", frequency_code="TID"))
    )

    linked_slots = {reminder.slot_type for reminder in reminders.created}
    assert linked_slots == {"morning", "noon", "evening"}
    assert len(reminders.links) == 3


@pytest.mark.asyncio
async def test_commit_result_exposes_the_reminder_ids_created_or_linked():
    """「已建立」的回應不能是黑箱：呼叫端必須能看到藥品實際掛在哪些提醒
    規則上，而不是被動信任一句成功訊息（見 find_or_create_reminder 的
    修正：命中一筆停用或過期的規則時不會重用它，這裡要確保呼叫端真的
    看得到最後掛上去的是哪一筆）。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1", "U_FAMILY", _request(CommitDrugItem(name="某藥", frequency_code="TID"))
    )

    assert set(result.reminder_ids) == {r.id for r in reminders.created}
    assert len(result.reminder_ids) == 3


@pytest.mark.asyncio
async def test_commit_result_reminder_ids_are_deduplicated():
    """兩顆藥若映射到同一個時段，那個時段只有一筆提醒規則——reminder_ids
    不該因為兩顆藥都連到它就重複出現兩次。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="藥一"), RecognizedDrug(name="藥二"))
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1",
        "U_FAMILY",
        _request(
            CommitDrugItem(name="藥一", frequency_code="QD"),
            CommitDrugItem(name="藥二", frequency_code="QD"),
        ),
    )

    assert len(reminders.links) == 2
    assert result.reminder_ids == [reminders.created[0].id]


@pytest.mark.asyncio
async def test_commit_result_exposes_reactivated_slots():
    """命中的既有規則原本不可排程（例如使用者先前手動關掉），這次提交把它
    改回可排程狀態——呼叫端（LIFF）要能從結果看到這件事，才能在送出後的
    訊息裡如實告知使用者，而不是讓提醒悄悄變回啟用。"""
    existing = MedicationReminder(
        id="R_MORNING",
        creator_user_id="U_FAMILY",
        user_id="U_PATIENT",
        slot_type="morning",
        enabled=False,
    )
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    reminders = FakeReminderRepository(existing=[existing], reactivate_slots={"morning"})
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1", "U_FAMILY", _request(CommitDrugItem(name="某藥", frequency_code="QD"))
    )

    assert result.reactivated_slots == ["morning"]


@pytest.mark.asyncio
async def test_commit_result_reactivated_slots_deduplicated():
    """兩顆藥都連到同一個已停用的時段：那個時段只重新開啟一次，
    reactivated_slots 不該因此重複出現兩次。"""
    existing = MedicationReminder(
        id="R_MORNING",
        creator_user_id="U_FAMILY",
        user_id="U_PATIENT",
        slot_type="morning",
        enabled=False,
    )
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="藥一"), RecognizedDrug(name="藥二"))
    reminders = FakeReminderRepository(existing=[existing], reactivate_slots={"morning"})
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1",
        "U_FAMILY",
        _request(
            CommitDrugItem(name="藥一", frequency_code="QD"),
            CommitDrugItem(name="藥二", frequency_code="QD"),
        ),
    )

    assert result.reactivated_slots == ["morning"]


@pytest.mark.asyncio
async def test_commit_result_reactivated_slots_empty_when_reminder_was_already_active():
    """對照組：命中的既有規則本來就可排程時，reactivated_slots 不該提到它——
    這件事根本沒有發生，訊息不能暗示使用者「有東西被重新開啟」。"""
    existing = MedicationReminder(
        id="R_MORNING",
        creator_user_id="U_FAMILY",
        user_id="U_PATIENT",
        slot_type="morning",
    )
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    reminders = FakeReminderRepository(existing=[existing])
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1", "U_FAMILY", _request(CommitDrugItem(name="某藥", frequency_code="QD"))
    )

    assert result.reactivated_slots == []


@pytest.mark.asyncio
async def test_commit_result_reminder_ids_empty_for_prn_only_submission():
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="止痛藥"))
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1", "U_FAMILY", _request(CommitDrugItem(name="止痛藥", frequency_code="PRN"))
    )

    assert result.reminder_ids == []


@pytest.mark.asyncio
async def test_commit_creates_medication_but_no_reminder_for_prn():
    """需要時才吃的備用藥若建成定時提醒，會使人依提醒定時服用備用藥。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="止痛藥"))
    reminders = FakeReminderRepository()
    medications = FakeMedicationRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        medications=medications,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1", "U_FAMILY", _request(CommitDrugItem(name="止痛藥", frequency_code="PRN"))
    )

    assert len(medications.created) == 1
    assert result.prn_medication_ids == result.medication_ids
    assert reminders.created == []
    assert reminders.links == []


@pytest.mark.asyncio
async def test_commit_ignores_user_supplied_slots_for_prn():
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="止痛藥"))
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1",
        "U_FAMILY",
        _request(
            CommitDrugItem(name="止痛藥", frequency_code="PRN", slots=["morning"])
        ),
    )

    assert reminders.links == []


@pytest.mark.asyncio
async def test_commit_rejects_unclassified_frequency_without_slots():
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    medications = FakeMedicationRepository()
    service = _service(
        drafts=drafts,
        medications=medications,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    with pytest.raises(SlotsRequiredError):
        await service.commit(
            "D1",
            "U_FAMILY",
            _request(CommitDrugItem(name="某藥", frequency_code="OTHER")),
        )

    assert medications.created == []


@pytest.mark.asyncio
async def test_commit_accepts_unclassified_frequency_with_explicit_slots():
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1",
        "U_FAMILY",
        _request(
            CommitDrugItem(name="某藥", frequency_code="OTHER", slots=["bedtime"])
        ),
    )

    assert [reminder.slot_type for reminder in reminders.created] == ["bedtime"]


@pytest.mark.asyncio
async def test_user_supplied_slots_override_the_frequency_mapping():
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1",
        "U_FAMILY",
        _request(CommitDrugItem(name="某藥", frequency_code="QD", slots=["bedtime"])),
    )

    assert [reminder.slot_type for reminder in reminders.created] == ["bedtime"]


@pytest.mark.asyncio
async def test_user_supplied_empty_slots_are_not_treated_as_no_override():
    """`slots=[]`（使用者在核對畫面上把每個時段都取消勾選）與 `slots=None`
    （前端沒有覆寫）是兩件不同的事：前者是明確表達「這顆藥不要定時提醒」，
    不能被當成後者、悄悄退回頻次代碼算出的預設時段——那正是使用者取消
    勾選的操作被無聲覆蓋。這顆藥仍然要被建立，只是不連結任何提醒，
    與 PRN 走到的結果一致。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    reminders = FakeReminderRepository()
    medications = FakeMedicationRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        medications=medications,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1",
        "U_FAMILY",
        _request(CommitDrugItem(name="某藥", frequency_code="QD", slots=[])),
    )

    assert len(medications.created) == 1
    assert reminders.created == []
    assert reminders.links == []
    # 不是 PRN，所以不會出現在 prn_medication_ids 裡——但它確實沒有任何提醒，
    # 這件事目前只由「沒有提醒被建立」這個事實本身反映，呼叫端（LIFF）
    # 在送出前就已經知道使用者取消勾選了什麼，不需要仰賴這裡的回應。
    assert result.prn_medication_ids == []
    assert result.reminder_ids == []


@pytest.mark.asyncio
async def test_omitted_slots_still_fall_back_to_the_frequency_mapping():
    """對照組：`slots=None`（前端真的沒有覆寫）必須維持原本的行為——
    落到頻次代碼映射出的預設時段，不能因為新增了「空陣列即拒絕自動映射」
    的規則就連 None 也一起被擋下。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1",
        "U_FAMILY",
        _request(CommitDrugItem(name="某藥", frequency_code="QD", slots=None)),
    )

    assert [reminder.slot_type for reminder in reminders.created] == ["morning"]


# --- timing 覆寫 QD 的預設時段 ---------------------------------------------


@pytest.mark.asyncio
async def test_qd_with_bedtime_timing_maps_to_bedtime_not_morning():
    """真實藥袋案例（冠脂妥膜衣錠，QD＋睡前服用）：頻次代碼本身只映射到
    `morning`，但辨識出的 timing 明確標示睡前，預設時段必須改為 `bedtime`，
    不能讓一顆睡前藥的預設提醒排在早上八點。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="冠脂妥膜衣錠10毫克"))
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1",
        "U_FAMILY",
        _request(
            CommitDrugItem(
                name="冠脂妥膜衣錠10毫克", frequency_code="QD", timing="bedtime"
            )
        ),
    )

    assert [reminder.slot_type for reminder in reminders.created] == ["bedtime"]


@pytest.mark.asyncio
async def test_qd_without_timing_still_maps_to_morning():
    """對照組：沒有 timing 的 QD 藥維持既有預設值，不能因為新增 timing
    覆寫規則而連「沒有 timing」的情況也被牽動。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1",
        "U_FAMILY",
        _request(CommitDrugItem(name="某藥", frequency_code="QD", timing=None)),
    )

    assert [reminder.slot_type for reminder in reminders.created] == ["morning"]


@pytest.mark.asyncio
async def test_qd_with_after_meal_timing_does_not_change_the_default_slot():
    """`before_meal`／`after_meal`／`empty_stomach` 描述的是與進食的關係，
    不指向任何特定時段，不得影響時段判定——只有 `bedtime` 才明確指向
    一個時段。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1",
        "U_FAMILY",
        _request(
            CommitDrugItem(name="某藥", frequency_code="QD", timing="after_meal")
        ),
    )

    assert [reminder.slot_type for reminder in reminders.created] == ["morning"]


@pytest.mark.asyncio
async def test_tid_with_bedtime_timing_keeps_the_three_usual_slots():
    """多劑量頻次（TID/BID/QID）即使 timing 是 `bedtime` 也不改寫映射——
    「睡前」標在多劑量藥袋上通常只限定最後一次劑量，頻次代碼是「一天
    吃幾次」這件事上更明確的陳述，不能被單一 timing 值覆寫掉整組時段。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1",
        "U_FAMILY",
        _request(
            CommitDrugItem(name="某藥", frequency_code="TID", timing="bedtime")
        ),
    )

    linked_slots = {reminder.slot_type for reminder in reminders.created}
    assert linked_slots == {"morning", "noon", "evening"}


@pytest.mark.asyncio
async def test_prn_with_bedtime_timing_still_gets_no_slots():
    """PRN 的安全規則優先於一切，包括 timing——需要時才吃的備用藥
    不論辨識出什麼 timing 都不建立定時提醒。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="止痛藥"))
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1",
        "U_FAMILY",
        _request(
            CommitDrugItem(name="止痛藥", frequency_code="PRN", timing="bedtime")
        ),
    )

    assert reminders.created == []
    assert result.reminder_ids == []


@pytest.mark.asyncio
async def test_explicit_slots_override_wins_over_bedtime_timing():
    """使用者在核對畫面明確覆寫過的 slots 永遠優先，即使 timing 指向
    `bedtime`、頻次也是 QD——這條規則已經存在，新增 timing 覆寫不能
    讓它退化。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1",
        "U_FAMILY",
        _request(
            CommitDrugItem(
                name="某藥",
                frequency_code="QD",
                timing="bedtime",
                slots=["morning"],
            )
        ),
    )

    assert [reminder.slot_type for reminder in reminders.created] == ["morning"]


@pytest.mark.asyncio
async def test_commit_reuses_an_existing_reminder_for_the_slot():
    """一位使用者一個時段只該有一筆規則；重複建立會讓長輩收到兩則提醒。"""
    existing = MedicationReminder(
        id="R_EXISTING",
        creator_user_id="U_FAMILY",
        user_id="U_PATIENT",
        slot_type="morning",
    )
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    reminders = FakeReminderRepository(existing=[existing])
    service = _service(
        drafts=drafts,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1", "U_FAMILY", _request(CommitDrugItem(name="某藥", frequency_code="QD"))
    )

    assert reminders.created == []
    assert reminders.links[0][0] == "R_EXISTING"


@pytest.mark.asyncio
async def test_commit_is_idempotent_when_the_draft_was_already_committed():
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    drafts.commit_result = (False, ["M_EXISTING"])
    medications = FakeMedicationRepository()
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        medications=medications,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1", "U_FAMILY", _request(CommitDrugItem(name="某藥", frequency_code="QD"))
    )

    assert result.medication_ids == ["M_EXISTING"]
    assert medications.created == []
    assert reminders.links == []


@pytest.mark.asyncio
async def test_commit_token_is_acquired_before_anything_is_written():
    """提交權必須帶著預先產生的藥品 id 取得。

    先建立再標記的話，兩個並行的提交會各建立一份藥品；而標記時若還不知道
    id，落敗的那一方會讀到空結果並誤以為沒有建立成功。
    """
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    medications = FakeMedicationRepository()
    service = _service(
        drafts=drafts,
        medications=medications,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1", "U_FAMILY", _request(CommitDrugItem(name="某藥", frequency_code="QD"))
    )

    assert drafts.commit_calls[0][2] == result.medication_ids
    assert [medication.id for medication in medications.created] == result.medication_ids


@pytest.mark.asyncio
async def test_commit_skips_drugs_the_user_excluded():
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    medications = FakeMedicationRepository()
    service = _service(
        drafts=drafts,
        medications=medications,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1",
        "U_FAMILY",
        _request(
            CommitDrugItem(name="要的藥", frequency_code="QD"),
            CommitDrugItem(name="不要的藥", frequency_code="QD", include=False),
        ),
    )

    assert [medication.name for medication in medications.created] == ["要的藥"]
    assert len(result.medication_ids) == 1


@pytest.mark.asyncio
async def test_commit_records_the_ocr_origin_on_created_medications():
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    medications = FakeMedicationRepository()
    service = _service(
        drafts=drafts,
        medications=medications,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1",
        "U_FAMILY",
        _request(
            CommitDrugItem(
                name="某藥", frequency_code="QD", usage_raw="QD PC", indication="高血壓"
            )
        ),
    )

    created = medications.created[0]
    assert created.source == "prescription_ocr"
    assert created.user_id == "U_PATIENT"
    assert created.created_by_user_id == "U_FAMILY"
    assert created.usage_raw == "QD PC"
    assert created.indication == "高血壓"


@pytest.mark.asyncio
async def test_commit_sets_end_date_from_duration_days():
    """5 天的療程從今天開始算，涵蓋今天起的 5 個整天，結束日是
    今天 + 4 天——沒有這個換算，每一份掃描出來的處方都會變成永久提醒
    （見 MedicationRepository.find_active_by_ids 的日期篩選：沒有 end_date
    就永遠是「有效」）。"""
    from app.models.medication import TAIPEI_TZ
    from datetime import datetime, timedelta

    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="安莫西林"))
    medications = FakeMedicationRepository()
    service = _service(
        drafts=drafts,
        medications=medications,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1",
        "U_FAMILY",
        _request(CommitDrugItem(name="安莫西林", frequency_code="TID", duration_days=5)),
    )

    created = medications.created[0]
    today = datetime.now(TAIPEI_TZ).date()
    assert created.start_date == today.strftime("%Y-%m-%d")
    assert created.end_date == (today + timedelta(days=4)).strftime("%Y-%m-%d")


@pytest.mark.asyncio
async def test_commit_leaves_end_date_open_without_duration_days():
    """沒有療程天數（慢性病長期用藥是常見情形）就不該臆測一個結束日期，
    這顆藥要維持長期有效。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="脈優錠"))
    medications = FakeMedicationRepository()
    service = _service(
        drafts=drafts,
        medications=medications,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1",
        "U_FAMILY",
        _request(CommitDrugItem(name="脈優錠", frequency_code="QD", duration_days=None)),
    )

    assert medications.created[0].end_date is None


@pytest.mark.asyncio
async def test_commit_releases_the_token_and_lets_a_retry_actually_create_when_writing_fails():
    """取得提交權之後、寫入時發生暫時性資料庫錯誤：這次要照樣把例外丟出去，
    但草稿的提交權必須被釋放，否則之後每次重試都會被 mark_committed 擋下，
    拿到一組其實從未寫入的 id 當成功回應——處方就這樣憑空消失。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    medications = FakeMedicationRepository(fail_times=1)
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        medications=medications,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )
    request = _request(CommitDrugItem(name="某藥", frequency_code="QD"))

    with pytest.raises(RuntimeError):
        await service.commit("D1", "U_FAMILY", request)

    # 第一次失敗：沒有任何藥品被建立，但提交權必須被釋放。
    assert medications.created == []
    assert len(drafts.release_calls) == 1
    assert drafts.release_calls[0][2] == drafts.commit_calls[0][2]
    assert drafts._committed_medication_ids is None

    # 重試：這次不再失敗，必須真的重新取得提交權並建立藥品與提醒。
    result = await service.commit("D1", "U_FAMILY", request)

    assert len(medications.created) == 1
    assert result.medication_ids == [medications.created[0].id]
    assert len(reminders.created) == 1


@pytest.mark.asyncio
async def test_commit_does_not_release_the_token_when_it_never_acquired_it():
    """沒有取得提交權時（草稿已被提交過）不該去釋放別人的提交權——
    這條路徑連寫入都沒有發生，release_commit 不該被呼叫。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(RecognizedDrug(name="某藥"))
    drafts.commit_result = (False, ["M_EXISTING"])
    service = _service(
        drafts=drafts,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    await service.commit(
        "D1", "U_FAMILY", _request(CommitDrugItem(name="某藥", frequency_code="QD"))
    )

    assert drafts.release_calls == []


@pytest.mark.asyncio
async def test_commit_replay_reports_prn_ids_consistently_with_the_original_commit():
    """重複 commit（例如使用者連點兩次）且其中一項是 PRN：第二次沒有取得
    提交權、完全不寫入任何東西，但只要重送的是同一份請求，位置對應仍然
    成立，回報的 prn_medication_ids 必須和第一次的結果一致，而不是因為
    『這次沒有機會自己建立』就退化成空陣列。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(
        RecognizedDrug(name="某藥"), RecognizedDrug(name="止痛藥")
    )
    medications = FakeMedicationRepository()
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        medications=medications,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )
    request = _request(
        CommitDrugItem(name="某藥", frequency_code="QD"),
        CommitDrugItem(name="止痛藥", frequency_code="PRN"),
    )

    first = await service.commit("D1", "U_FAMILY", request)
    second = await service.commit("D1", "U_FAMILY", request)

    assert second.medication_ids == first.medication_ids
    assert len(first.prn_medication_ids) == 1
    assert second.prn_medication_ids == first.prn_medication_ids
    # 第二次沒有取得提交權：不該再建立藥品，也不該再呼叫提醒關聯。
    assert len(medications.created) == 2
    assert len(reminders.find_or_create_calls) == 1
