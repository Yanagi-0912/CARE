from datetime import datetime, timedelta, timezone

import pytest

from app.models.family_tree import FamilyMember, FamilyTree
from app.models.medication import MedicationReminder
from app.models.prescription import (
    CommitDrugItem,
    CommitPrescriptionDraftRequest,
    DrugCandidate,
    PrescriptionDraft,
    RecognitionResult,
    RecognizedDrug,
)
from app.services.medication.drug_catalog_service import DrugCatalogEntry, DrugCatalogMatch
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


class FakeAppearanceImageResolver:
    """以字典模擬「證號 → 縮圖 URL」的查表，貼合正式服務
    （resolve_drug_appearance_image_url）查無縮圖時回 None 的介面，
    不必碰檔案系統或 monkeypatch settings 單例。"""

    def __init__(self, urls: dict[str, str] | None = None):
        self._urls = urls or {}

    def __call__(self, license_number: str) -> str | None:
        return self._urls.get(license_number)


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
    appearance_images=None,
    indications=None,
):
    return PrescriptionScanService(
        ocr_service=ocr or FakeOcr(RecognitionResult()),
        catalog_service=catalog or FakeCatalog(),
        draft_repository=drafts or FakeDraftRepository(),
        medication_repository=medications or FakeMedicationRepository(),
        reminder_repository=reminders or FakeReminderRepository(),
        family_tree_repository=family or FakeFamilyTreeRepository(),
        appearance_image_resolver=appearance_images or FakeAppearanceImageResolver(),
        ttl_minutes=60,
        indication_service=indications,
    )


def _recognition(*drugs: RecognizedDrug, patient_name="王大明") -> RecognitionResult:
    return RecognitionResult(
        institution="臺大醫院",
        patient_name=patient_name,
        dispensed_date="2026-08-09",
        drugs=list(drugs),
    )


def _match(name="脈優錠5毫克") -> DrugCatalogMatch:
    """單一命中的比對結果。

    真正的 `DrugCatalogService._resolve()` 在 `license_number` 有值時，
    `candidates` 一定剛好帶著那一張藥證（唯一命中時也是只含一筆的清單，
    見該方法與 DrugCatalogMatch 的說明）——`license_number` 有值卻
    `candidates` 是空清單，是 `_resolve()` 絕對不會產生的狀態。這個
    fixture 過去沒有帶 candidates，若繼續留空，一旦有程式碼開始依賴
    「證號有值 ⇒ 該證號在候選清單內」這個不變式（例如提交時的候選驗證），
    這裡就測不出問題——用這個假造的 match 餵出去的 RecognizedDrug 反而會
    是候選模型上線前才可能出現的舊式資料，卻被當成「今天掃描產生的正常
    結果」在測。這裡補上同一張藥證的 DrugCatalogEntry，讓 fixture 忠實
    反映真正的比對結果。
    """
    entry = DrugCatalogEntry(
        license_number="衛署藥製字第000001號",
        name_zh=name,
        name_en="AMLODIPINE TABLETS 5MG",
    )
    return DrugCatalogMatch(
        license_number=entry.license_number,
        name_zh=name,
        name_en="AMLODIPINE TABLETS 5MG",
        score=1.0,
        candidates=[entry],
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
async def test_scan_marks_high_name_confidence_when_catalog_hit_has_multiple_candidates():
    """藥證庫索引改為候選模型後（決策 1），`match()` 命中碰撞鍵時
    `license_number` 會是 None、`candidates` 有不只一筆。名稱信心度的
    判定只看 `match()` 是不是 None（`_verify_against_catalog` 的既有
    註解已載明此意圖），這裡直接餵一筆多候選的比對結果，釘住信心度
    不會因為候選不只一張就被拖累成低信心——一張藥證命中 41 個候選
    仍然是一個被驗證為真實存在的藥名。

    同時釘住候選清單本身正確地落到草稿上（spec「草稿攜帶藥證候選清單」）：
    證號、中文品名、外觀欄位都要原樣帶過，縮圖 URL 則由掃描當下注入的
    resolver 就地解析——其中一張候選（第二張）在 resolver 裡查無縮圖，
    驗證這種情況下 `thumbnail_url` 為 None 且不影響其餘候選。"""
    drug = RecognizedDrug(name="葉酸", frequency_code="TID")
    entry_with_photo = DrugCatalogEntry(
        license_number="衛署藥製字第040001號",
        name_zh="葉酸",
        shape="圓形",
        color="白色",
        score_line="有",
        mark_one="F1",
        mark_two="",
        size="8mm",
    )
    entry_without_photo = DrugCatalogEntry(
        license_number="衛署藥輸字第040002號", name_zh="葉酸"
    )
    multi_candidate_match = DrugCatalogMatch(
        license_number=None,
        name_zh="",
        name_en="",
        score=1.0,
        candidates=[entry_with_photo, entry_without_photo],
    )
    service = _service(
        ocr=FakeOcr(_recognition(drug)),
        catalog=FakeCatalog({"葉酸": multi_candidate_match}),
        family=FakeFamilyTreeRepository(
            _tree(FamilyMember(user_id="U_PATIENT", display_name="王大明"))
        ),
        appearance_images=FakeAppearanceImageResolver(
            {"衛署藥製字第040001號": "https://static.example/a3f1.jpg"}
        ),
    )

    draft = await service.scan(b"image", "image/jpeg", "U_FAMILY")

    assert draft.recognition.drugs[0].name_confidence == "high"
    assert draft.recognition.drugs[0].license_number is None
    # 名稱信心度不受候選數量拖累，草稿整體信心度也應維持最高等級——
    # 其餘條件（頻次已知、用藥對象已建議）都滿足時就是 high。
    assert draft.confidence_level == "high"

    candidates = draft.recognition.drugs[0].candidates
    assert [c.license_number for c in candidates] == [
        "衛署藥製字第040001號",
        "衛署藥輸字第040002號",
    ]
    first = candidates[0]
    assert first.name_zh == "葉酸"
    assert first.shape == "圓形"
    assert first.color == "白色"
    assert first.score_line == "有"
    assert first.mark_one == "F1"
    assert first.size == "8mm"
    assert first.thumbnail_url == "https://static.example/a3f1.jpg"
    # 查無縮圖的候選：thumbnail_url 為 None，且不影響其餘欄位或這筆候選
    # 本身出現在清單裡——查無照片不是把候選整筆丟掉的理由。
    second = candidates[1]
    assert second.license_number == "衛署藥輸字第040002號"
    assert second.thumbnail_url is None


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


# --- commit：使用者挑定的候選證號 ------------------------------------------


def _candidate(license_number="L1", name_zh="某藥", **overrides) -> DrugCandidate:
    fields = dict(
        shape="圓形",
        color="白色",
        score_line="有",
        mark_one="M1",
        mark_two="M2",
        size="8mm",
        thumbnail_url="https://static.example/thumb.jpg",
    )
    fields.update(overrides)
    return DrugCandidate(license_number=license_number, name_zh=name_zh, **fields)


@pytest.mark.asyncio
async def test_commit_accepts_a_license_number_within_the_candidates():
    """挑定候選清單內的證號：建立的藥品要帶著這個證號，外觀欄位也要原樣
    帶自該候選（spec「提交時接受使用者挑定的藥證」）。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(
        RecognizedDrug(
            name="某藥",
            candidates=[_candidate(license_number="L1"), _candidate(license_number="L2")],
        )
    )
    medications = FakeMedicationRepository()
    service = _service(
        drafts=drafts,
        medications=medications,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1",
        "U_FAMILY",
        _request(CommitDrugItem(name="某藥", frequency_code="QD", license_number="L1")),
    )

    assert len(result.medication_ids) == 1
    created = medications.created[0]
    assert created.license_number == "L1"
    assert created.shape == "圓形"
    assert created.color == "白色"
    assert created.score_line == "有"
    assert created.mark_one == "M1"
    assert created.mark_two == "M2"
    assert created.size == "8mm"


@pytest.mark.asyncio
async def test_commit_discards_a_license_number_outside_the_candidates():
    """帶回不在該筆藥品候選清單內的證號：SHALL NOT 拒絕整份提交（spec
    「提交時接受使用者挑定的藥證」已修訂），而是丟棄該證號、以空證號建立
    這一筆，其餘欄位不受影響；丟棄的結果要點名在回應裡，不能靜默發生。
    候選清單外的值實務上只來自用戶端瑕疵，或使用者改名後證號未隨之清空
    ——兩者都不該讓使用者連同已核對過的其他藥品一併失去。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(
        RecognizedDrug(name="某藥", candidates=[_candidate(license_number="L1")])
    )
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
            CommitDrugItem(
                name="某藥", frequency_code="QD", license_number="L_NOT_A_CANDIDATE"
            )
        ),
    )

    assert len(medications.created) == 1
    created = medications.created[0]
    assert created.license_number is None
    assert result.medication_ids == [created.id]
    assert result.discarded_license_medication_ids == [created.id]


@pytest.mark.asyncio
async def test_commit_discarded_license_does_not_inherit_another_candidates_appearance():
    """被丟棄的挑選不能誤繼承候選清單裡任何一張的外觀——候選清單本身不是
    空的、候選確實帶著看得出來的外觀資料，丟棄後這一筆仍然要是完全空白
    的外觀，而不是意外選到清單中的某一張（例如「就近取第一筆」這類錯誤
    的容錯邏輯）。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(
        RecognizedDrug(
            name="某藥",
            candidates=[
                _candidate(license_number="L1", shape="圓形", color="白色"),
                _candidate(license_number="L2", shape="星形", color="藍色"),
            ],
        )
    )
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
                name="某藥", frequency_code="QD", license_number="L_NOT_A_CANDIDATE"
            )
        ),
    )

    created = medications.created[0]
    assert created.license_number is None
    assert created.shape == ""
    assert created.color == ""
    assert created.score_line == ""
    assert created.mark_one == ""
    assert created.mark_two == ""
    assert created.size == ""


@pytest.mark.asyncio
async def test_commit_discards_one_drugs_license_while_the_rest_commit_normally():
    """多筆藥品中只有一筆挑定的證號落在候選外：那一筆以空證號建立，其餘
    藥品正常建立（不因此株連），回應要點名哪一筆被丟棄——spec 明文要求
    SHALL NOT 因此拒絕整份提交，且 SHALL 於回應中列出被丟棄的藥品。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(
        RecognizedDrug(name="藥一", candidates=[_candidate(license_number="L1")]),
        RecognizedDrug(
            name="藥二", candidates=[_candidate(license_number="L2", name_zh="藥二")]
        ),
    )
    medications = FakeMedicationRepository()
    reminders = FakeReminderRepository()
    service = _service(
        drafts=drafts,
        medications=medications,
        reminders=reminders,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1",
        "U_FAMILY",
        _request(
            CommitDrugItem(name="藥一", frequency_code="QD", license_number="L_BAD"),
            CommitDrugItem(name="藥二", frequency_code="QD", license_number="L2"),
        ),
    )

    assert len(medications.created) == 2
    by_name = {medication.name: medication for medication in medications.created}
    assert by_name["藥一"].license_number is None
    assert by_name["藥一"].shape == ""
    assert by_name["藥二"].license_number == "L2"
    assert by_name["藥二"].shape == "圓形"  # 正常挑定的那一筆外觀照常帶入

    assert result.discarded_license_medication_ids == [by_name["藥一"].id]
    # 丟棄不影響其他行為：兩顆藥都是 QD、都應該照常連結到提醒。
    assert len(reminders.links) == 2


@pytest.mark.asyncio
async def test_commit_drops_the_pre_migration_license_number_when_a_legacy_draft_has_no_candidates():
    """本次部署前就存在的草稿，讀回時 RecognizedDrug.candidates 是空清單
    （欄位在那之前不存在，pydantic 補上預設值），只留著一個 license_number。
    **那個證號必須被丟掉。**

    先前這裡放行它，理由寫的是「同一份 ground truth，只是還沒有候選清單
    這個表達方式」。那個理由是錯的：部署前的 `match()` 在正規化鍵碰撞時，
    索引以 `setdefault` 只留得下第一筆，回傳的是那個鍵上 N 張藥證裡**任意
    的一張**（「感冒液」一個鍵就有 41 張），而不是驗證過的答案——那正是本
    change 要消滅的 4.8% 錯配。放行它等於讓舊索引的瑕疵繞過新的安全邊界，
    而且讀取時證號會單獨解析出縮圖，畫面上沒有任何外觀文字能跟它牴觸，
    長輩只會看到一張沒有東西反駁的錯照片。

    丟棄不列入 `discarded_license_medication_ids`：核對畫面在候選為空時
    整段外觀區塊都不呈現，使用者根本沒有東西可挑，用戶端只是把草稿裡既有
    的值原樣送回來。告訴他「你的挑選被丟棄了」是假的，這是 spec 的
    「未挑選」而不是「丟棄」。代價是部署前 TTL window 內的草稿沒有照片，
    正是 spec「照片缺席時的降級」規定的方向。

    直接用一份「沒有 candidates 鍵」的原始 dict 餵給 `PrescriptionDraft(
    **doc)`，模擬從 Mongo 讀回舊文件、pydantic 以預設值補上空清單的真實
    情形（`PRESCRIPTION_DRAFT_TTL_MINUTES` window 內，部署前掃描、部署後
    提交的草稿正是這個形狀）。"""
    now = datetime.now(timezone.utc)
    legacy_doc = {
        "draft_id": "D1",
        "creator_user_id": "U_FAMILY",
        "recognition": {
            "institution": "臺大醫院",
            "patient_name": "王大明",
            "dispensed_date": "2026-08-09",
            "drugs": [
                {
                    "name": "脈優錠5毫克",
                    "frequency_code": "QD",
                    "license_number": "衛署藥製字第000001號",
                    "name_confidence": "high",
                    # 刻意不帶 "candidates" 鍵——模擬這個欄位上線前寫入的文件。
                }
            ],
        },
        "confidence_level": "high",
        "created_at": now,
        "expires_at": now + timedelta(minutes=60),
    }
    draft = PrescriptionDraft(**legacy_doc)
    # 佐證這確實是「沒有候選清單」的舊式草稿，不是這個測試自己餵錯資料。
    assert draft.recognition.drugs[0].candidates == []

    drafts = FakeDraftRepository()
    drafts.draft = draft
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
            CommitDrugItem(
                name="脈優錠5毫克",
                frequency_code="QD",
                license_number="衛署藥製字第000001號",
            )
        ),
    )

    # 提交照常成功——照片是附加價值，舊草稿不得因此被拒絕。
    assert len(medications.created) == 1
    created = medications.created[0]
    # 舊索引任意挑出來的證號不得落地，落地了就會單獨解析出一張錯的縮圖。
    assert created.license_number is None
    # 沒有候選物件可用（舊式草稿沒有留下外觀資料），外觀欄位維持空。
    assert created.shape == ""
    # 使用者從來沒有挑過任何東西，不能對他宣稱「你的挑選被丟棄了」。
    assert result.discarded_license_medication_ids == []
    # 其餘欄位不受影響：這顆 QD 藥照常連結到提醒。
    assert len(result.reminder_ids) == 1


@pytest.mark.asyncio
async def test_commit_discloses_the_discard_when_the_name_is_absent_from_the_draft():
    """藥名根本不在草稿裡（改名後證號未隨之清空）：證號要丟掉，而且要
    **揭露**——`discarded_license_medication_ids` 必須點名這一筆。

    這是「候選桶是空的」與「這個藥名根本不在草稿裡」兩件事的分界：
    - 桶存在但是空的 → 走「未挑選」，不揭露（上一個測試）。使用者面前
      根本沒有外觀區塊，說「你的挑選被丟棄了」是假的。
    - 桶不存在（`candidates_by_name.get(name)` 是 None）→ 走「丟棄」，
      要揭露。使用者確實挑過東西，只是他接著把藥名改成了別的字串。
      `_resolve_candidate` 的 docstring 把改名列為這條路徑最主要的
      真實來源。

    這條路徑先前沒有任何測試覆蓋：把 `_resolve_candidate` 的
    `if bucket is not None and not bucket:` 改成 `if not bucket:`，
    全檔仍然全綠——但改完之後這一筆會被當成「未挑選」，
    `discarded_license_medication_ids` 空著、`commit()` 的 WARNING
    （它自己的註解稱為「後端唯一能觀察到它的地方」）永遠不會響，
    用戶端瑕疵就此完全隱形。證號一樣不落地，所以不會貼錯照片；
    掉的是揭露。
    """
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(
        RecognizedDrug(name="某藥", candidates=[_candidate(license_number="L1")])
    )
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
            # 藥名被改過，草稿裡沒有這個名字；證號卻還是原本那一筆的。
            CommitDrugItem(name="改過的藥名", frequency_code="QD", license_number="L1")
        ),
    )

    assert len(medications.created) == 1
    created = medications.created[0]
    assert created.license_number is None
    assert created.shape == ""
    assert result.medication_ids == [created.id]
    # 承重的斷言：丟棄必須被點名，不得靜默。
    assert result.discarded_license_medication_ids == [created.id]


@pytest.mark.asyncio
async def test_commit_without_choosing_a_license_still_succeeds():
    """未挑選 SHALL NOT 阻擋提交：該筆以 license_number 為空建立，外觀欄位
    留空，其餘欄位不受影響（spec「使用者為多候選藥品挑定藥證」場景
    「使用者未挑選」）。"""
    drafts = FakeDraftRepository()
    drafts.draft = _stored_draft(
        RecognizedDrug(
            name="某藥",
            candidates=[_candidate(license_number="L1"), _candidate(license_number="L2")],
        )
    )
    medications = FakeMedicationRepository()
    service = _service(
        drafts=drafts,
        medications=medications,
        family=FakeFamilyTreeRepository(_tree(FamilyMember(user_id="U_PATIENT"))),
    )

    result = await service.commit(
        "D1", "U_FAMILY", _request(CommitDrugItem(name="某藥", frequency_code="QD"))
    )

    assert len(result.medication_ids) == 1
    created = medications.created[0]
    assert created.license_number is None
    assert created.shape == ""
    assert created.color == ""
    assert created.score_line == ""
    assert created.mark_one == ""
    assert created.mark_two == ""
    assert created.size == ""


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


# ── 仿單適應症比對：只記錄，不影響任何判定 ──────────────────────────


class FakeIndicationService:
    """以固定回傳值餵比對結果（DI，不 monkey patch）。"""

    def __init__(self, verdict: str = "unrelated"):
        self.verdict = verdict
        self.calls: list[tuple] = []

    def compare(self, bag_indication, license_number):
        self.calls.append((bag_indication, license_number))
        return self.verdict


@pytest.mark.asyncio
async def test_unrelated_indication_still_yields_high_confidence():
    """spec scenario：判定不相干仍維持高信心。

    這是本能力最重要的一條保證。比對規則的誤判率尚未以真實藥袋量測（模擬
    落在 17%~25%），而信心度要求全部藥品皆通過——若讓它參與判定，一顆誤判
    就會讓整份草稿失去一鍵確認。這個測試存在的目的，就是讓將來任何「順手」
    把 indication_match 接進 all_names_verified 的改動立刻失敗。
    """
    drug = RecognizedDrug(name="脈優錠5毫克", frequency_code="TID", indication="降血壓")
    service = _service(
        ocr=FakeOcr(_recognition(drug)),
        catalog=FakeCatalog({"脈優錠5毫克": _match()}),
        family=FakeFamilyTreeRepository(
            _tree(FamilyMember(user_id="U_PATIENT", display_name="王大明"))
        ),
        indications=FakeIndicationService("unrelated"),
    )

    draft = await service.scan(b"image", "image/jpeg", "U_FAMILY")

    assert draft.recognition.drugs[0].indication_match == "unrelated"
    assert draft.confidence_level == "high"


@pytest.mark.asyncio
async def test_unrelated_indication_does_not_change_name_confidence():
    """spec scenario：不改變名稱信心度。

    名稱信心度只該由藥證庫校驗決定——那是唯一能發現模型錯讀形近藥名的手段。
    """
    drug = RecognizedDrug(name="脈優錠5毫克", frequency_code="TID", indication="降血壓")
    service = _service(
        ocr=FakeOcr(_recognition(drug)),
        catalog=FakeCatalog({"脈優錠5毫克": _match()}),
        indications=FakeIndicationService("unrelated"),
    )

    draft = await service.scan(b"image", "image/jpeg", "U_FAMILY")

    assert draft.recognition.drugs[0].name_confidence == "high"


@pytest.mark.asyncio
async def test_indication_match_defaults_to_unchecked_without_service():
    """未注入仿單服務時整個步驟略過，行為與本能力導入前完全相同。"""
    drug = RecognizedDrug(name="脈優錠5毫克", frequency_code="TID", indication="降血壓")
    service = _service(
        ocr=FakeOcr(_recognition(drug)),
        catalog=FakeCatalog({"脈優錠5毫克": _match()}),
    )

    draft = await service.scan(b"image", "image/jpeg", "U_FAMILY")

    assert draft.recognition.drugs[0].indication_match == "unchecked"


@pytest.mark.asyncio
async def test_comparison_receives_bag_indication_and_resolved_license():
    """比對拿到的必須是藥袋讀出的適應症與**校驗後**的證號，不是原始輸入。"""
    drug = RecognizedDrug(name="脈優錠5毫克", frequency_code="TID", indication="降血壓")
    fake = FakeIndicationService("consistent")
    service = _service(
        ocr=FakeOcr(_recognition(drug)),
        catalog=FakeCatalog({"脈優錠5毫克": _match()}),
        indications=fake,
    )

    draft = await service.scan(b"image", "image/jpeg", "U_FAMILY")

    assert fake.calls == [("降血壓", draft.recognition.drugs[0].license_number)]


# ── 姓名比對的候選範圍受寫入權限縮（tasks 8.7／8.14）──────────────────


class FakeAuthorizationService:
    """只回答「操作者對某位對象有沒有 GENERAL 寫入權」。"""

    def __init__(self, writable: set[str]):
        self._writable = writable

    async def can(self, operator_id, target_owner_id, classification, action):
        return target_owner_id in self._writable


async def test_name_match_skips_members_without_write_permission():
    """姓名命中但無寫入權時 SHALL NOT 成為預設對象。

    提出一個使用者確認後必定被 403 擋下的建議，只是讓他在藥袋辨識這一步
    多撞一次牆——長輩的照顧者在這裡已經在對抗光線與字級了。
    """
    service = _service(
        ocr=FakeOcr(_recognition(RecognizedDrug(name="脈優錠5毫克", frequency_code="TID"), patient_name="王大明")),
        family=FakeFamilyTreeRepository(
            _tree(FamilyMember(user_id="U_ELDER", display_name="王大明"))
        ),
    )
    service._authorization_service = FakeAuthorizationService(writable=set())

    draft = await service.scan(b"image", "image/jpeg", "U_OPERATOR")

    assert draft.suggested_user_id is None


async def test_name_match_keeps_members_with_write_permission():
    """對照組：有寫入權時仍是預設對象，行為與變更前相同。"""
    service = _service(
        ocr=FakeOcr(_recognition(RecognizedDrug(name="脈優錠5毫克", frequency_code="TID"), patient_name="王大明")),
        family=FakeFamilyTreeRepository(
            _tree(FamilyMember(user_id="U_ELDER", display_name="王大明"))
        ),
    )
    service._authorization_service = FakeAuthorizationService(writable={"U_ELDER"})

    draft = await service.scan(b"image", "image/jpeg", "U_OPERATOR")

    assert draft.suggested_user_id == "U_ELDER"


async def test_name_match_without_authorization_service_keeps_legacy_behaviour():
    """未注入授權服務時只影響**預設值**，不影響授權。

    真正的閘門在 commit（router 的 authorize 與服務層的
    TargetNotInFamilyError），這裡放行不等於提交會過。
    """
    service = _service(
        ocr=FakeOcr(_recognition(RecognizedDrug(name="脈優錠5毫克", frequency_code="TID"), patient_name="王大明")),
        family=FakeFamilyTreeRepository(
            _tree(FamilyMember(user_id="U_ELDER", display_name="王大明"))
        ),
    )

    draft = await service.scan(b"image", "image/jpeg", "U_OPERATOR")

    assert draft.suggested_user_id == "U_ELDER"
