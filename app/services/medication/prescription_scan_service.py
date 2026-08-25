"""藥袋掃描的協調服務。

辨識、藥證庫比對、族譜比對、草稿存取、藥品與提醒建立全部已經存在；本服務
不新增任何一步的邏輯，只負責決定這些既有的服務／repository 之間資料如何
流動、以什麼順序呼叫。刻意保持「薄」——邏輯越薄，將來任何一步規則變動時
需要改的地方就越少、越集中。
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Protocol

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
    DrugCandidate,
    PrescriptionCommitResult,
    PrescriptionDraft,
    RecognitionResult,
    RecognizedDrug,
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


class _IndicationService(Protocol):
    def compare(self, bag_indication, license_number): ...


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


# 證號 -> 對外縮圖 URL（查無縮圖回 None）。以純函式簽章注入而非直接依賴
# drug_appearance_image_service 模組，測試才能餵一個字典查表的假實作，
# 不必碰檔案系統或 monkeypatch settings 單例；正式組裝時直接把
# resolve_drug_appearance_image_url 這個函式本身傳進來即可（其餘參數
# 皆有預設值，讀 app.core.config.settings）。
_AppearanceImageResolver = Callable[[str], Optional[str]]


@dataclass(frozen=True)
class _ResolvedCandidate:
    """`_resolve_candidate` 的回傳值。

    `license_number` 與 `candidate` 分開追蹤，是因為「這筆藥最終該落地的
    證號」跟「有沒有候選外觀資料可用」是兩件事：證號落地時一定伴隨一個
    真正的 `DrugCandidate`（外觀欄位由它帶入），沒挑選或挑選被丟棄時
    兩者一起是 None——**不存在「有證號但沒有候選物件」的組合**，那正是
    舊索引任意挑一張藥證留下的形狀，會讓證號單獨解析出一張沒有任何外觀
    文字能佐證的照片，見 `_candidates_by_name`。
    `discarded` 標記這筆挑選是否因為不在候選清單內而被丟棄，供 `commit()`
    記錄與回報，不能靜默發生。
    """

    license_number: Optional[str]
    candidate: Optional[DrugCandidate]
    discarded: bool


class PrescriptionScanService:
    def __init__(
        self,
        ocr_service: _OcrService,
        catalog_service: _CatalogService,
        draft_repository: _DraftRepository,
        medication_repository: _MedicationRepository,
        reminder_repository: _ReminderRepository,
        family_tree_repository: _FamilyTreeRepository,
        appearance_image_resolver: _AppearanceImageResolver,
        ttl_minutes: int,
        indication_service: Optional[_IndicationService] = None,
        authorization_service: Any = None,
    ) -> None:
        self._ocr_service = ocr_service
        self._catalog_service = catalog_service
        self._draft_repository = draft_repository
        self._medication_repository = medication_repository
        self._reminder_repository = reminder_repository
        self._family_tree_repository = family_tree_repository
        # 選填：未注入時姓名比對不做權限篩選（僅影響**預設值**，不影響授權；
        # 提交的閘門在 router 的 authorize 與下方的 TargetNotInFamilyError）。
        self._authorization_service = authorization_service
        # 選填：未注入時比對一律 unchecked，行為與本變更前完全相同。
        # 單元測試因此不必為了測辨識流程而準備一份仿單資料。
        self._indication_service = indication_service
        self._appearance_image_resolver = appearance_image_resolver
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

        # 仿單比對就地記錄在每一筆上。刻意放在信心度計算「之前」，是為了讓
        # 下面那三行的運算式讀起來就能看出它沒有參與其中——比對結果 SHALL NOT
        # 影響信心度，理由見 _record_indication_match 的說明。
        self._record_indication_match(recognition)

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

    def _record_indication_match(self, recognition: RecognitionResult) -> None:
        """逐筆記錄藥袋適應症與仿單的比對結果。**只記錄，不影響任何判定。**

        沒有注入仿單服務時整個步驟略過，每一筆維持預設的 unchecked——這讓
        既有測試與未載入仿單資料的環境行為完全不變。

        為什麼不接上信心度：本規則的誤判率尚未以真實藥袋量測過。以「藥袋
        短語對仿單長文」模擬，誤判率落在 17%~25%；而 scan() 的信心度要求
        **全部**藥品皆通過，一顆誤判就會讓整份草稿失去一鍵確認——三種藥的
        藥袋維持高信心的機率僅約 51%。用一個測不準的規則去拆斷已經調校好的
        確認路徑，付出的代價大於它能擋下的錯誤。待累積真實資料、量出實際
        誤判率後，再以另一個 change 評估是否接上。
        """
        if self._indication_service is None:
            return
        for drug in recognition.drugs:
            drug.indication_match = self._indication_service.compare(
                drug.indication, drug.license_number
            )

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
            # 把 match() 已經算好的候選集合原樣搬到草稿上（唯一命中時也是只含
            # 一筆的清單，見 DrugCatalogMatch 的說明），供核對畫面呈現候選的
            # 照片與外觀描述；縮圖 URL 在這裡就地解析一次，呈現面不必再另外
            # 查一次檔案系統或組路徑。
            drug.candidates = [
                DrugCandidate(
                    license_number=entry.license_number,
                    name_zh=entry.name_zh,
                    shape=entry.shape,
                    color=entry.color,
                    score_line=entry.score_line,
                    mark_one=entry.mark_one,
                    mark_two=entry.mark_two,
                    size=entry.size,
                    thumbnail_url=self._appearance_image_resolver(entry.license_number),
                )
                for entry in match.candidates
            ]
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
            if member.display_name != patient_name:
                continue
            # 比對範圍限於操作者**代得了**的成員：提出一個使用者確認後必定
            # 被 403 擋下的建議，只是讓他在藥袋辨識這一步多撞一次牆。真正的
            # 閘門在 commit，這裡只是不要先給錯的預設值。
            if self._authorization_service is not None:
                if not await self._authorization_service.can(
                    user_id, member.user_id, "GENERAL", "WRITE"
                ):
                    continue
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

        # 先把每一項要建立的藥決定好時段（需要時段卻沒有就整批拒絕），並解析
        # 挑定的證號是否落在候選清單內——這一步 SHALL NOT 拒絕整份提交，見
        # `_resolve_candidate` 的說明；此時還沒產生任何 id，也還沒寫入任何
        # 東西。
        candidates_by_name = self._candidates_by_name(draft.recognition.drugs)
        resolved = [
            (item, self._resolve_slots(item), self._resolve_candidate(item, candidates_by_name))
            for item in payload.drugs
            if item.include
        ]

        # 丟棄不能是靜默的：使用者明確選過的東西被系統丟掉，即使不擋提交，
        # 也要留下痕跡——回應裡的 discarded_license_medication_ids 讓呼叫端
        # 能告知使用者，這裡的 log 讓後端能觀察到頻率。候選外的證號正常情況
        # 只來自使用者改名後證號未清除；若同一用戶端反覆出現，代表它沒有
        # 依照候選清單提交，值得排查——用 WARNING 而非 DEBUG，因為這一步
        # 拒絕整份提交的舊行為拿掉之後，用戶端瑕疵不會再以 400 現身，記錄
        # 是後端唯一能觀察到它的地方。
        discarded_names = [
            item.name
            for item, _slots, resolved_candidate in resolved
            if resolved_candidate.discarded
        ]
        if discarded_names:
            logger.warning(
                "草稿 %s 提交時有 %d 筆藥品挑定的證號不在候選清單內，已丟棄改以空證號建立：%s",
                draft_id,
                len(discarded_names),
                discarded_names,
            )

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
                discarded_license_medication_ids=self._discarded_license_ids(
                    resolved, final_medication_ids
                ),
            )

        # 取得提交權與實際寫入之間沒有原子性；建立藥品或連結提醒若因暫時性
        # 資料庫錯誤而拋出，草稿會停在「已提交」但底下的藥品其實沒有寫入，
        # 之後每次重試都會被 mark_committed 擋下、拿到一組從未真正建立的
        # id 當成功回應——處方就這樣憑空消失，沒有任何補救路徑。因此失敗
        # 時把提交權還給草稿，讓例外照樣往外拋（呼叫端知道這次沒有成功），
        # 下一次重試才能真的重新取得提交權、重新寫入。
        try:
            medications = [
                self._build_medication(
                    item, resolved_candidate, medication_id, target_user_id, user_id
                )
                for (item, _slots, resolved_candidate), medication_id in zip(
                    resolved, medication_ids
                )
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
            discarded_license_medication_ids=self._discarded_license_ids(resolved, medication_ids),
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
    def _candidates_by_name(
        drugs: list[RecognizedDrug],
    ) -> dict[str, dict[str, DrugCandidate]]:
        """把草稿裡每一筆辨識藥品的候選清單，整理成「藥名 → {證號: 候選}」，
        供 `_resolve_candidate` 查詢使用。

        同一個藥名理論上不會出現兩筆候選不同的辨識結果——`match()` 對同一個
        字串永遠算出同一個候選集合——這裡仍以合併而非覆蓋處理，純粹保守。
        **這個假設只在候選清單本身完整反映當時 match() 結果時成立。** 如果
        未來候選清單會被進一步收窄後再寫回單一 item（例如以顏色／形狀縮小
        候選，見 spec「候選過多時以外觀屬性漸進收窄」），同名的兩筆
        RecognizedDrug 可能各自只剩下收窄後的子集合，屆時這裡的合併會把
        已經收窄過的清單悄悄擴回聯集，等於讓收窄失效——修改前務必先確認
        候選是否仍可安全合併。

        **合法選項只來自 `drug.candidates`，不含 `drug.license_number`。**
        曾經在候選清單為空時額外把既有的 `license_number` 登記成合法選項，
        理由是「那是候選模型導入前的同一份 ground truth」。那個理由是錯的：
        本次部署前的 `match()` 在正規化鍵碰撞時回傳的是**該鍵上 N 張藥證裡
        任意的一張**（`setdefault` 只留得下第一筆），而不是驗證過的答案——
        「感冒液」這種鍵一次就有 41 張。那個任意值正是本 change 要消滅的
        4.8% 錯配，把它放行等於讓舊索引的瑕疵繞過新的安全邊界，而且它會在
        讀取時由證號單獨解析出縮圖，畫面上沒有任何外觀文字能跟它牴觸——
        長輩只會看到一張沒有東西反駁的錯照片。
        代價是部署前 `PRESCRIPTION_DRAFT_TTL_MINUTES` 內掃描的草稿沒有照片，
        這正是 spec「照片缺席時的降級」規定的安全退化方向。

        **給下一個改動 `match()` 候選語意的人**：`commit()` 不會重跑
        `match()`，合法選項完全來自草稿**寫入當時**存下的 `candidates`。
        也就是說，草稿存下的候選只在「產生它的那套 `match()` 語意」下才是
        安全的；語意一改，TTL 內既存的草稿就會帶著舊語意的候選活過部署，
        而新版的前端照樣把它們渲染成可挑選的照片卡、新版的後端照樣收下。
        實測過一次：把反向含容命中排除出 `candidates` 的那次變更，若當時
        候選欄位已在正式環境存在，部署後一個 TTL 內使用者仍可挑到
        「注射液」這類別的藥的證號與外觀，且 `discarded` 為 False、不會揭露。
        當時不成立的原因只是候選欄位本身跟那次變更同屬一次合併，正式環境
        的舊草稿根本沒有這個欄位（＝走上面那條丟棄路徑）——這是巧合，
        不是保護。下次沒有這個巧合時，必須連同 TTL 內的既存草稿一起處理
        （提交時重跑 `match()` 取交集、或替草稿標上語意版本並讓舊版失效）。
        """
        by_name: dict[str, dict[str, DrugCandidate]] = {}
        for drug in drugs:
            # 每個藥名都先建一個（可能是空的）桶：空桶跟「這個藥名根本不在
            # 草稿裡」是兩件不同的事，`_resolve_candidate` 靠這個差別區分
            # 「沒得挑」與「挑了清單外的值」。
            bucket = by_name.setdefault(drug.name, {})
            for candidate in drug.candidates:
                bucket[candidate.license_number] = candidate
        return by_name

    @staticmethod
    def _resolve_candidate(
        item: CommitDrugItem,
        candidates_by_name: dict[str, dict[str, DrugCandidate]],
    ) -> _ResolvedCandidate:
        """決定這筆藥最終該落地的證號、外觀候選，以及挑選是否被丟棄。

        未挑選（`item.license_number` 為空）一律合法：不落地任何證號，也
        沒有候選可用——挑選是附加價值，不是提交的必要條件（spec「使用者
        為多候選藥品挑定藥證」）。

        挑選了卻在候選清單裡查不到——候選清單裡沒有這個證號，或藥名已被
        改成候選清單之外的字串——SHALL NOT 拒絕整份提交（spec「提交時接受
        使用者挑定的藥證」修訂後的理由：候選外的證號實務上只來自用戶端
        瑕疵，或使用者改名後證號未隨之清空，兩者都不該讓使用者連同已核對
        過的其他藥品一併失去）。這裡改為丟棄，回傳 `discarded=True`，交由
        `commit()` 記錄並在回應中揭露——**丟棄的是使用者做過的挑選，不能
        靜默發生**。

        但「這個藥名的候選清單是空的」是另一回事，走的是「未挑選」而不是
        「丟棄」：核對畫面在候選為空時整段外觀區塊都不呈現（見 LIFF 的
        `DrugCandidateSection`），使用者根本沒有東西可挑，用戶端回傳的
        證號只是把草稿裡既有的值原樣送回來。這種情形最主要的來源是本次
        部署前寫入的舊草稿（`candidates` 欄位當時還不存在），那個證號是
        舊索引在鍵碰撞時任意挑的一張，不可信、必須丟掉（見
        `_candidates_by_name`），但對使用者揭露「你的挑選被丟棄了」是假的
        ——他沒有挑過任何東西。因此靜默落成空證號，等同 spec 的「未挑選」。
        """
        if not item.license_number:
            return _ResolvedCandidate(license_number=None, candidate=None, discarded=False)
        bucket = candidates_by_name.get(item.name)
        if bucket is not None and not bucket:
            # 這個藥名在草稿裡，但一張候選都沒有——沒得挑，見上面的說明。
            return _ResolvedCandidate(license_number=None, candidate=None, discarded=False)
        if bucket is None or item.license_number not in bucket:
            return _ResolvedCandidate(license_number=None, candidate=None, discarded=True)
        return _ResolvedCandidate(
            license_number=item.license_number,
            candidate=bucket[item.license_number],
            discarded=False,
        )

    @staticmethod
    def _prn_ids(
        resolved: list[tuple[CommitDrugItem, list[MedicationSlotType], _ResolvedCandidate]],
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
            for (item, _slots, _resolved_candidate), medication_id in zip(resolved, medication_ids)
            if item.frequency_code == "PRN"
        ]

    @staticmethod
    def _discarded_license_ids(
        resolved: list[tuple[CommitDrugItem, list[MedicationSlotType], _ResolvedCandidate]],
        medication_ids: list[str],
    ) -> list[str]:
        """從這次的 payload 推算哪些建立出來的藥品被丟棄了挑定的證號。

        與 `_prn_ids` 適用同樣的位置對應限制與理由：只有在冪等回放重送的
        是同一份請求、長度對得上時，`resolved` 與 `medication_ids` 的位置
        對應才可靠；長度不對時已經沒有依據能從位置猜出哪些 id 對應到被
        丟棄的證號，寧可誠實回空，也不要讓呼叫端誤以為某顆藥有／沒有被
        丟棄證號。
        """
        if len(medication_ids) != len(resolved):
            return []
        return [
            medication_id
            for (item, _slots, resolved_candidate), medication_id in zip(resolved, medication_ids)
            if resolved_candidate.discarded
        ]

    @staticmethod
    def _build_medication(
        item: CommitDrugItem,
        resolved_candidate: _ResolvedCandidate,
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
        candidate = resolved_candidate.candidate
        return Medication(
            id=medication_id,
            user_id=target_user_id,
            created_by_user_id=creator_user_id,
            name=item.name,
            generic_name=item.generic_name,
            # 從 `_resolved_candidate.license_number` 取值，不直接複製
            # `item.license_number`——後者是使用者端原始輸入，未挑選是 None、
            # 但用戶端若傳空字串（""）也會落到這裡，`_resolve_candidate` 已
            # 把「沒挑選」與「挑了卻不合法／被丟棄」都收斂成 None，這個不變式
            # 只要在這裡讀 `_resolved_candidate` 就自動成立，不必在這裡另外
            # 判斷空字串。
            license_number=resolved_candidate.license_number,
            # 外觀欄位只在有候選物件可用（candidate 非 None）時才有值，原樣
            # 帶自對應候選——未挑選、或挑選被丟棄、或只命中舊式草稿的相容
            # 項目（沒有候選物件）時，candidate 都是 None，維持 Medication
            # 欄位的預設空字串，不臆測外觀、也不會誤繼承別的候選。
            shape=candidate.shape if candidate else "",
            color=candidate.color if candidate else "",
            score_line=candidate.score_line if candidate else "",
            mark_one=candidate.mark_one if candidate else "",
            mark_two=candidate.mark_two if candidate else "",
            size=candidate.size if candidate else "",
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
        resolved: list[tuple[CommitDrugItem, list[MedicationSlotType], _ResolvedCandidate]],
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
        for (item, slots, _candidate), medication_id in zip(resolved, medication_ids):
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
