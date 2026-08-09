"""藥袋掃描的協調服務。

辨識、藥證庫比對、族譜比對、草稿存取、藥品與提醒建立全部已經存在；本服務
不新增任何一步的邏輯，只負責決定這些既有的服務／repository 之間資料如何
流動、以什麼順序呼叫。刻意保持「薄」——邏輯越薄，將來任何一步規則變動時
需要改的地方就越少、越集中。
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

from bson import ObjectId

from app.models.medication import (
    DEFAULT_SLOT_TIMES,
    Medication,
    MedicationReminder,
    MedicationSlotType,
    ensure_aware_utc,
)
from app.models.prescription import (
    FREQUENCY_TO_SLOTS,
    CommitDrugItem,
    CommitPrescriptionDraftRequest,
    PrescriptionCommitResult,
    PrescriptionDraft,
    RecognitionResult,
)
from app.services.medication.drug_catalog_service import DrugCatalogMatch

logger = logging.getLogger(__name__)


class DraftNotFoundError(Exception):
    """草稿不存在，或不屬於這位使用者。兩種情況合併回報同一個例外——
    分別回報會讓這個端點成為探測他人草稿是否存在的管道。"""


class DraftExpiredError(Exception):
    """草稿已過 TTL；辨識結果多半也已經過時，必須請使用者重新掃描。"""


class SlotsRequiredError(Exception):
    """OTHER 頻次沒有可映射的時段，使用者也沒有指定——拒絕提交，不臆測時段。"""


class TargetNotInFamilyError(Exception):
    """指定的用藥對象不是本人，也不在建立者的族譜內。"""


class _OcrService(Protocol):
    async def recognize(self, image_bytes: bytes, mime_type: str) -> RecognitionResult: ...


class _CatalogService(Protocol):
    def match(self, name: str) -> Optional[DrugCatalogMatch]: ...


class _DraftRepository(Protocol):
    async def create(self, draft: PrescriptionDraft) -> PrescriptionDraft: ...

    async def find_by_id_for_user(
        self, draft_id: str, user_id: str
    ) -> Optional[PrescriptionDraft]: ...

    async def mark_committed(
        self, draft_id: str, user_id: str, medication_ids: list[str]
    ) -> tuple[bool, list[str]]: ...


class _MedicationRepository(Protocol):
    async def create_many(self, medications: list[Medication]) -> list[Medication]: ...


class _ReminderRepository(Protocol):
    async def list_reminders_by_user(self, user_id: str) -> list[MedicationReminder]: ...

    async def create_reminder(self, reminder: MedicationReminder) -> MedicationReminder: ...

    async def link_medications_to_reminder(
        self, reminder_id: str, medication_ids: list[str]
    ) -> bool: ...


class _FamilyTreeRepository(Protocol):
    async def get_by_user_id(self, user_id: str): ...


class PrescriptionScanService:
    def __init__(
        self,
        ocr_service: _OcrService,
        catalog_service: _CatalogService,
        draft_repository: _DraftRepository,
        medication_repository: _MedicationRepository,
        reminder_repository: _ReminderRepository,
        family_tree_repository: _FamilyTreeRepository,
        ttl_minutes: int,
    ) -> None:
        self._ocr_service = ocr_service
        self._catalog_service = catalog_service
        self._draft_repository = draft_repository
        self._medication_repository = medication_repository
        self._reminder_repository = reminder_repository
        self._family_tree_repository = family_tree_repository
        self._ttl_minutes = ttl_minutes

    # ── 掃描 ────────────────────────────────────────────────────────

    async def scan(
        self, image_bytes: bytes, mime_type: str, user_id: str
    ) -> PrescriptionDraft:
        """辨識藥袋、逐筆校驗藥名、建議用藥對象，最後存成草稿。

        PrescriptionUnreadableError / PrescriptionNotRecognizedError /
        PrescriptionServiceUnavailableError 原樣往外拋，不在這裡攔截——
        路由層要依三種原因分別給使用者不同的下一步指示（重拍／換一張／
        稍後再試），在這裡吞掉或改寫例外類型會讓那個判斷做不了。
        """
        recognition = await self._ocr_service.recognize(image_bytes, mime_type)

        all_names_verified = bool(recognition.drugs)
        for drug in recognition.drugs:
            match = self._catalog_service.match(drug.name)
            if match is None:
                all_names_verified = False
                continue
            drug.license_number = match.license_number
            # 未經藥證庫校驗一律低信心（RecognizedDrug 的預設值）；
            # 只有比對命中才升到高信心，這是唯一能發現模型錯讀形近藥名的手段。
            drug.name_confidence = "high"

        all_frequencies_known = bool(recognition.drugs) and all(
            drug.frequency_code != "OTHER" for drug in recognition.drugs
        )

        suggested_user_id = await self._suggest_user_id(recognition.patient_name, user_id)

        # 一鍵確認只在「藥名全部通過校驗」且「用藥對象與頻次皆已確定」時開放；
        # 少任何一項都代表有東西需要人工核對，寧可多一次確認也不要建錯提醒。
        confidence_level = (
            "high"
            if all_names_verified and all_frequencies_known and suggested_user_id is not None
            else "medium"
        )

        draft = PrescriptionDraft(
            draft_id=str(ObjectId()),
            creator_user_id=user_id,
            recognition=recognition,
            confidence_level=confidence_level,
            suggested_user_id=suggested_user_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=self._ttl_minutes),
        )
        return await self._draft_repository.create(draft)

    async def _suggest_user_id(
        self, patient_name: Optional[str], user_id: str
    ) -> Optional[str]:
        """以藥袋上的病患姓名比對操作者的族譜成員姓名，命中則回傳其 user_id。

        只是預設值：呼叫端絕不能拿這個值直接建立提醒，必須等使用者在核對
        畫面上確認（或改選）之後，才會出現在 commit 的 payload 裡。
        """
        if not patient_name:
            return None
        tree = await self._family_tree_repository.get_by_user_id(user_id)
        if tree is None:
            return None
        for member in tree.family_members:
            if member.display_name == patient_name:
                return member.user_id
        return None

    # ── 提交 ────────────────────────────────────────────────────────

    async def commit(
        self,
        draft_id: str,
        user_id: str,
        payload: CommitPrescriptionDraftRequest,
    ) -> PrescriptionCommitResult:
        draft = await self._draft_repository.find_by_id_for_user(draft_id, user_id)
        if draft is None:
            raise DraftNotFoundError(draft_id)
        if ensure_aware_utc(draft.expires_at) <= datetime.now(timezone.utc):
            raise DraftExpiredError(draft_id)

        target_user_id = payload.user_id
        if target_user_id != user_id:
            tree = await self._family_tree_repository.get_by_user_id(user_id)
            if not tree or not any(
                member.user_id == target_user_id for member in tree.family_members
            ):
                raise TargetNotInFamilyError(target_user_id)

        # 先把每一項要建立的藥決定好時段（或確認不需要時段），任何一項
        # 需要時段卻沒有就整批拒絕——此時還沒產生任何 id，也還沒寫入任何東西。
        resolved = [
            (item, self._resolve_slots(item))
            for item in payload.drugs
            if item.include
        ]

        # 提交權必須帶著預先產生好的藥品 id 一起取得，而不是先建立藥品再標記。
        # 「先建立再標記」的話，兩個並行的提交會各自建立一份完整的藥品；
        # 標記時才知道 id 的話，落敗的一方讀到的是自己那份而非贏家的結果。
        medication_ids = [str(ObjectId()) for _ in resolved]
        acquired, final_medication_ids = await self._draft_repository.mark_committed(
            draft_id, user_id, medication_ids
        )
        if not acquired:
            # 沒取得提交權：這份草稿已經被（正在被）提交過。直接回傳既有結果，
            # 不再建立任何藥品或提醒——這是冪等保證的核心。
            return PrescriptionCommitResult(
                medication_ids=final_medication_ids,
                prn_medication_ids=self._prn_ids(resolved, final_medication_ids),
            )

        medications = [
            self._build_medication(item, medication_id, target_user_id, user_id)
            for (item, _slots), medication_id in zip(resolved, medication_ids)
        ]
        await self._medication_repository.create_many(medications)

        await self._link_reminders(resolved, medication_ids, target_user_id, user_id)

        return PrescriptionCommitResult(
            medication_ids=medication_ids,
            prn_medication_ids=self._prn_ids(resolved, medication_ids),
        )

    def _resolve_slots(
        self, item: CommitDrugItem
    ) -> list[MedicationSlotType]:
        """決定一項藥品要關聯到哪些時段。PRN 一律不關聯任何時段——

        「需要時才吃」的備用藥若被排進固定時段，會讓人依提醒定時服用備用藥，
        這是安全規則，不是偏好，即使使用者手動指定了 slots 也不理會。
        """
        if item.frequency_code == "PRN":
            return []
        if item.slots:
            return list(item.slots)
        mapped = FREQUENCY_TO_SLOTS.get(item.frequency_code, ())
        if not mapped:
            # OTHER（或任何未來新增但尚未映射的代碼）沒有預設時段可用，
            # 使用者又沒有手動指定——寧可拒絕提交，也不要猜一個服藥時間。
            raise SlotsRequiredError(item.name)
        return list(mapped)

    @staticmethod
    def _prn_ids(
        resolved: list[tuple[CommitDrugItem, list[MedicationSlotType]]],
        medication_ids: list[str],
    ) -> list[str]:
        # 冪等回放時 final_medication_ids 理論上與這次的 payload 一一對應
        # （重送的就是同一份請求）；長度對不上代表拿到的是别次提交的結果，
        # 這時沒有可靠的方式從位置猜出哪些是 PRN，寧可回空也不要亂猜。
        if len(medication_ids) != len(resolved):
            return []
        return [
            medication_id
            for (item, _slots), medication_id in zip(resolved, medication_ids)
            if item.frequency_code == "PRN"
        ]

    @staticmethod
    def _build_medication(
        item: CommitDrugItem,
        medication_id: str,
        target_user_id: str,
        creator_user_id: str,
    ) -> Medication:
        return Medication(
            id=medication_id,
            user_id=target_user_id,
            created_by_user_id=creator_user_id,
            name=item.name,
            generic_name=item.generic_name,
            license_number=item.license_number,
            unit_content=item.unit_content,
            total_quantity=item.total_quantity,
            usage_raw=item.usage_raw,
            frequency_code=item.frequency_code,
            indication=item.indication,
            source="prescription_ocr",
        )

    async def _link_reminders(
        self,
        resolved: list[tuple[CommitDrugItem, list[MedicationSlotType]]],
        medication_ids: list[str],
        target_user_id: str,
        creator_user_id: str,
    ) -> None:
        """把非 PRN 的藥關聯到對應時段的提醒，沿用既有的建立路徑：
        同一個 (使用者, 時段) 已有規則就只做關聯，沒有才新建一筆——兩筆規則
        對應同一個時段會讓長輩收到兩則推播。"""
        existing_by_slot: dict[str, MedicationReminder] = {
            reminder.slot_type: reminder
            for reminder in await self._reminder_repository.list_reminders_by_user(
                target_user_id
            )
        }

        for (item, slots), medication_id in zip(resolved, medication_ids):
            if item.frequency_code == "PRN":
                continue
            for slot in slots:
                reminder = existing_by_slot.get(slot)
                if reminder is None:
                    reminder = await self._reminder_repository.create_reminder(
                        MedicationReminder(
                            creator_user_id=creator_user_id,
                            user_id=target_user_id,
                            slot_type=slot,
                            scheduled_time=DEFAULT_SLOT_TIMES.get(slot, "08:00"),
                        )
                    )
                    existing_by_slot[slot] = reminder
                await self._reminder_repository.link_medications_to_reminder(
                    reminder.id, [medication_id]
                )
