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
    TAIPEI_TZ,
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


def _today_date_str() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


def _end_date_from_duration(start_date: str, duration_days: int) -> str:
    """把療程天數換算成結束日期，起始日算療程的第一天。

    5 天的療程從 start_date 當天開始服用，涵蓋 start_date 起的 5 個整天
    （第 1～5 天），因此結束日是 start_date + 4 天，而不是 + 5 天。
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = start + timedelta(days=duration_days - 1)
    return end.strftime("%Y-%m-%d")


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

    async def release_commit(
        self, draft_id: str, user_id: str, medication_ids: list[str]
    ) -> bool: ...


class _MedicationRepository(Protocol):
    async def create_many(self, medications: list[Medication]) -> list[Medication]: ...


class _ReminderRepository(Protocol):
    async def find_or_create_reminder(
        self,
        user_id: str,
        slot_type: str,
        creator_user_id: str,
        scheduled_time: str,
    ) -> tuple[MedicationReminder, bool]: ...

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

        all_names_verified = self._verify_against_catalog(recognition)

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

    def _verify_against_catalog(self, recognition: RecognitionResult) -> bool:
        """逐筆用藥證庫校驗辨識出的藥名，就地更新每一筆的信心度與證號。

        `match()` 回傳非 None 就代表藥名已驗證為真實存在的核准藥品——
        這是唯一能發現模型錯讀形近藥名的手段，因此無論 `license_number`
        有沒有值都要升到高信心。`license_number` 可能是 None：這是含容
        比對命中不只一張藥證時的正常結果（例如「普拿疼」同時是好幾個
        普拿疼系列產品品名的子字串），代表藥名本身沒問題、只是不知道
        對應哪一個品項，不是校驗失敗，見 DrugCatalogMatch 的說明。
        """
        all_verified = bool(recognition.drugs)
        for drug in recognition.drugs:
            match = self._catalog_service.match(drug.name)
            if match is None:
                all_verified = False
                continue
            drug.license_number = match.license_number
            # 未經藥證庫校驗一律低信心（RecognizedDrug 的預設值）；
            # 只有比對命中才升到高信心，這是唯一能發現模型錯讀形近藥名的手段。
            drug.name_confidence = "high"
        return all_verified

    async def get_draft(self, draft_id: str, user_id: str) -> PrescriptionDraft:
        """讀回先前掃描產生的草稿，供核對畫面重新載入時使用。

        找不到與「找到但不是這位使用者的」統一回 DraftNotFoundError——
        由路由層一律轉成 404，不能讓這支端點變成探測他人草稿是否存在的管道。
        """
        draft = await self._draft_repository.find_by_id_for_user(draft_id, user_id)
        if draft is None:
            raise DraftNotFoundError(draft_id)
        return draft

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
            # 冪等重放同樣拿不回當初那次提交實際重新開啟了哪些時段——
            # 與 prn_medication_ids 在同一情境下的處理方式一致，回空陣列
            # 而不是猜測，理由見該欄位重放分支的說明。
            return PrescriptionCommitResult(
                medication_ids=final_medication_ids,
                prn_medication_ids=self._prn_ids(resolved, final_medication_ids),
            )

        # 取得提交權與實際寫入之間沒有原子性；建立藥品或連結提醒若因暫時性
        # 資料庫錯誤而拋出，草稿會停在「已提交」但底下的藥品其實沒有寫入，
        # 之後每次重試都會被 mark_committed 擋下、拿到一組從未真正建立的
        # id 當成功回應——處方就這樣憑空消失，沒有任何補救路徑。因此失敗
        # 時把提交權還給草稿，讓例外照樣往外拋（呼叫端知道這次沒有成功），
        # 下一次重試才能真的重新取得提交權、重新寫入。
        try:
            medications = [
                self._build_medication(item, medication_id, target_user_id, user_id)
                for (item, _slots), medication_id in zip(resolved, medication_ids)
            ]
            await self._medication_repository.create_many(medications)

            reminder_ids, reactivated_slots = await self._link_reminders(
                resolved, medication_ids, target_user_id, user_id
            )
        except Exception:
            await self._draft_repository.release_commit(draft_id, user_id, medication_ids)
            raise

        return PrescriptionCommitResult(
            medication_ids=medication_ids,
            prn_medication_ids=self._prn_ids(resolved, medication_ids),
            reminder_ids=reminder_ids,
            reactivated_slots=reactivated_slots,
        )

    def _resolve_slots(
        self, item: CommitDrugItem
    ) -> list[MedicationSlotType]:
        """決定一項藥品要關聯到哪些時段。PRN 一律不關聯任何時段——

        「需要時才吃」的備用藥若被排進固定時段，會讓人依提醒定時服用備用藥，
        這是安全規則，不是偏好，即使使用者手動指定了 slots 也不理會。

        `item.slots is None`（前端沒有覆寫）與 `item.slots == []`
        （使用者在核對畫面上把每個時段都取消勾選）SHALL NOT 視為同一件事：
        前者才落到頻次映射（或下方的 timing 覆寫）的預設值，後者是使用者
        明確表達「這顆藥不要定時提醒」的選擇，必須原樣尊重、不得悄悄退回
        預設時段——否則使用者取消勾選的操作會被無聲地覆蓋，這顆藥因此
        排進了他明確拒絕的時段。

        `timing == "bedtime"` 只在頻次代碼是「一日單一劑量」（目前僅 QD；
        `HS` 本身就已映射到 `bedtime`，不受影響）時才改寫預設時段為
        `bedtime`——這是唯一一種 timing 能明確指向單一時段的情況。
        `before_meal`／`after_meal`／`empty_stomach` 描述的是與進食的
        關係，不是哪一個時段，一律不影響映射；`BID`／`TID`／`QID` 這類
        多劑量頻次即使 timing 是 `bedtime` 也維持原有映射不變——「睡前」
        標在多劑量藥袋上通常只限定最後一次劑量，不能因此把整組時段都
        改寫，頻次代碼是「一天吃幾次」這件事上更明確的陳述。
        """
        if item.frequency_code == "PRN":
            return []
        if item.slots is not None:
            return list(item.slots)
        if item.frequency_code == "QD" and item.timing == "bedtime":
            return ["bedtime"]
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
        """從這次的 payload 推算哪些建立出來的藥品屬於 PRN。

        正常提交時 resolved 與 medication_ids 一定等長、順序一一對應——
        兩者是同一個 zip 循環的兩端，這裡永遠會走 if 分支。

        冪等回放（mark_committed 回報 acquired=False）時，medication_ids
        換成了「原本那次提交」留下的 id，如果重送的是同一份請求，長度自然
        還是對得上；長度對不上，代表這次的 payload 跟真正建立那批藥時的
        payload 形狀不同（例如兩個分頁對同一份草稿送出了不同的勾選結果，
        其中一個順利建立、另一個才在這裡讀到贏家的結果）。這時已經沒有
        任何依據能從位置猜出贏家那批 id 裡哪些是 PRN——這個回放分支只是
        把既有結果如實回報，並不會再去建立或連結任何提醒，medication_ids
        本身（呼叫端真正需要拿去做事的那個欄位）不受影響，錯的只會是
        prn_medication_ids 這個純資訊性欄位；猜錯了會讓呼叫端誤以為某顆
        藥有／沒有對應提醒，比誠實回報「不知道」更容易誤導，所以刻意回空。
        """
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
        """組出要寫入的 Medication。

        start_date 固定為今天（提交當下），end_date 由 duration_days 換算——
        沒有 duration_days（慢性病長期用藥是常見情形）就不設 end_date，
        維持長期有效，不臆測一個療程終點。start_date 與 end_date 的換算
        必須用同一個「今天」，避免兩次各自呼叫 `_today_date_str()` 在極端
        情況下跨過午夜而算出不一致的結果。
        """
        start_date = _today_date_str()
        end_date = (
            _end_date_from_duration(start_date, item.duration_days)
            if item.duration_days and item.duration_days > 0
            else None
        )
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
            start_date=start_date,
            end_date=end_date,
        )

    async def _link_reminders(
        self,
        resolved: list[tuple[CommitDrugItem, list[MedicationSlotType]]],
        medication_ids: list[str],
        target_user_id: str,
        creator_user_id: str,
    ) -> tuple[list[str], list[MedicationSlotType]]:
        """把非 PRN 的藥關聯到對應時段的提醒。

        回傳 `(reminder_ids, reactivated_slots)`：
        - `reminder_ids` 是實際建立或連結到的提醒 id（去重）——「藥品建立
          成功」不等於「會被排程器推播」，呼叫端必須能看到藥品實際掛在
          哪些提醒規則上，而不是被動信任一句「已建立」。
        - `reactivated_slots` 是這次提交把哪些時段從「停用／已過期／還沒
          到 start_date」重新變回可排程狀態（去重）。find_or_create_reminder
          只保證一個時段最多一份規則，不保證那份規則原本就是活的——命中
          一筆原本關閉的規則時會直接把它改回可排程，順帶恢復掛在它底下、
          使用者當初就是要停掉的其他藥。這件事不能悄悄發生，呼叫端要能
          把它組進 PrescriptionCommitResult，讓使用者在核對畫面事先看到、
          送出後的訊息也如實反映。

        取得（或建立）提醒規則本身走 find_or_create_reminder 的原子 upsert，
        不是「先查一次現有規則、缺席才建立」——先查後建的模式在兩個並行的
        提交競爭同一個 (使用者, 時段) 時，會讓兩邊都在查詢當下判斷缺席而
        各自建立一筆，使用者因此收到兩則同一時段的推播。這個競態不是
        mark_committed 的 CAS 能擋下的：那個 CAS 只保護「同一份草稿被重複
        提交」，不同的兩份草稿（例如兩位家屬各自掃描同一位長輩、或同一人
        連續掃了兩張藥袋）本來就都會走到這裡，各自合法地想要同一個時段。
        """
        reminder_ids: list[str] = []
        reactivated_slots: list[MedicationSlotType] = []
        for (item, slots), medication_id in zip(resolved, medication_ids):
            if item.frequency_code == "PRN":
                continue
            for slot in slots:
                reminder, reactivated = await self._reminder_repository.find_or_create_reminder(
                    user_id=target_user_id,
                    slot_type=slot,
                    creator_user_id=creator_user_id,
                    scheduled_time=DEFAULT_SLOT_TIMES.get(slot, "08:00"),
                )
                await self._reminder_repository.link_medications_to_reminder(
                    reminder.id, [medication_id]
                )
                if reminder.id not in reminder_ids:
                    reminder_ids.append(reminder.id)
                if reactivated and slot not in reactivated_slots:
                    reactivated_slots.append(slot)
        return reminder_ids, reactivated_slots
