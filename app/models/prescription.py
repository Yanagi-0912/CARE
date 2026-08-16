"""藥袋辨識的結果、草稿與其列舉型別。

辨識結果與正式的用藥資料刻意分開：辨識產物一律先落在草稿，使用者核對並提交
之後才寫入 medications 與 medication_reminders。把兩者放同一個集合再以狀態
欄位區分，會讓每一個讀取藥品的地方都得記得過濾狀態，漏一處就會讓未確認的
辨識結果流進推播。
"""

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.medication import MedicationFrequencyCode, MedicationSlotType

# 醫囑頻次。與 Medication 共用同一份定義，避免兩邊各自演化而失去同步。
FrequencyCode = MedicationFrequencyCode

# 服用時機。藥袋未標示時為 None，不推測。
DrugTiming = Literal["before_meal", "after_meal", "bedtime", "empty_stomach"]

ConfidenceLevel = Literal["high", "medium", "low"]

# 辨識失敗的原因。三者對使用者的下一步指示完全不同（重拍／換一張／稍後再試），
# 合併成同一則訊息會讓使用者重複做無效的重拍。
ScanFailureReason = Literal["unreadable", "not_prescription", "service_unavailable"]

# 頻次代碼到提醒時段的映射。
# PRN 為空：需要時才吃的備用藥若建成定時提醒，會使人依提醒定時服用備用藥。
# OTHER 為空：無法歸類的頻次由使用者指定時段，不猜。
# 值為 tuple 而非 list——這個映射是共用的，不可變才能保證呼叫端無法就地修改。
FREQUENCY_TO_SLOTS: dict[str, tuple[MedicationSlotType, ...]] = {
    "QD": ("morning",),
    "BID": ("morning", "evening"),
    "TID": ("morning", "noon", "evening"),
    "QID": ("morning", "noon", "evening", "bedtime"),
    "HS": ("bedtime",),
    "PRN": (),
    "OTHER": (),
}


class DrugCandidate(BaseModel):
    """單一候選藥證：藥名命中多張藥證時，供核對畫面呈現給使用者挑選的其中一張。

    欄位原樣帶自 DrugCatalogEntry（見該類別的說明），外觀欄位缺席時留空字串
    而非 None——呼叫端（Flex 訊息、LIFF 清單）不必先判斷型別就能安全串接顯示。
    `thumbnail_url` 是 drug_appearance_image_service 在掃描當下就地解析出的
    對外縮圖路徑，查無縮圖時為 None，呈現面據此安全地退回純文字（spec
    「照片缺席時的降級」），不必等到呈現當下才去檢查檔案是否存在。
    """

    license_number: str
    name_zh: str
    shape: str = ""
    color: str = ""
    score_line: str = ""
    mark_one: str = ""
    mark_two: str = ""
    size: str = ""
    thumbnail_url: Optional[str] = None


class RecognizedDrug(BaseModel):
    """單一藥品的辨識結果。除藥名外全部允許為空——欄位缺漏時留空，不填推測值。"""

    name: str
    generic_name: Optional[str] = None
    unit_content: Optional[str] = None
    total_quantity: Optional[int] = None
    # 藥袋上的用法原文，原樣保留。核對時使用者要對照的是藥袋實際印的字串；
    # 只給正規化後的頻次代碼，他無從判斷正規化本身有沒有錯。
    usage_raw: Optional[str] = None
    frequency_code: FrequencyCode = "OTHER"
    dose_per_time: Optional[str] = None
    timing: Optional[DrugTiming] = None
    duration_days: Optional[int] = None
    indication: Optional[str] = None
    # 藥證庫比對命中後才會有值
    license_number: Optional[str] = None
    # 藥名命中多張藥證時的候選清單（見 DrugCatalogMatch.candidates 的說明）；
    # 唯一命中時仍是只含一筆的清單，不受影響。核對畫面用它呈現候選的照片與
    # 外觀描述供使用者挑選；挑選結果經由 CommitDrugItem.license_number 送回，
    # 並在 commit() 時校驗必須落在這份清單之內——這是那道校驗唯一的
    # ground truth，見 PrescriptionScanService._resolve_candidate。
    candidates: list[DrugCandidate] = Field(default_factory=list)
    # 未經藥證庫校驗一律低信心。視覺模型讀錯形近藥名時自述信心度仍然很高，
    # 只有外部字典能發現該字串不對應任何一張核准藥證。
    name_confidence: ConfidenceLevel = "low"


class RecognitionResult(BaseModel):
    institution: Optional[str] = None
    patient_name: Optional[str] = None
    dispensed_date: Optional[str] = None
    drugs: list[RecognizedDrug] = Field(default_factory=list)
    # 出現多個病患姓名或多份調劑日期時為 True：單張影像裡可能鋪了好幾個藥袋。
    multiple_bags_suspected: bool = False


class PrescriptionDraft(BaseModel):
    """待使用者核對的辨識草稿。以 TTL 自動過期。"""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    draft_id: str
    creator_user_id: str
    recognition: RecognitionResult
    confidence_level: ConfidenceLevel
    # 由藥袋病患姓名比對族譜得到的建議對象。僅為預設值，未經使用者確認
    # 不得據以建立任何提醒。
    suggested_user_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    committed_at: Optional[datetime] = None
    committed_medication_ids: list[str] = Field(default_factory=list)


class CommitDrugItem(BaseModel):
    """使用者核對草稿後，確認要建立的單一藥品。

    欄位涵蓋 Medication 真正會寫入的部分。duration_days 會換算成
    Medication.end_date，決定這顆藥何時自動停止提醒，因此必須帶著
    使用者在核對畫面上看過（並可能修正過）的值一起送出。timing 同樣
    有明確去處——單一每日劑量（QD）標示「睡前」時，決定預設時段要用
    `bedtime` 而非頻次代碼本身映射出的 `morning`，見
    `prescription_scan_service._resolve_slots`；不是純顯示欄位。
    """

    name: str
    generic_name: Optional[str] = None
    # 前端把使用者在候選清單中挑定的證號原樣回傳；留空代表未挑選，兩者皆合法
    # （見 spec「使用者為多候選藥品挑定藥證」：未挑選 SHALL NOT 阻擋提交）。
    # 提交階段不會拿（可能已被使用者改過的）藥名重新比對出新證號——那應該是
    # 下一次掃描的責任；但會反過來驗證帶回的值是否落在草稿當初那筆藥品的
    # 候選清單內，候選清單是這條路徑上唯一的 ground truth，不在清單內的值
    # 一律拒絕且不寫入任何東西，見 PrescriptionScanService._resolve_candidate。
    license_number: Optional[str] = None
    unit_content: Optional[str] = None
    total_quantity: Optional[int] = None
    usage_raw: Optional[str] = None
    frequency_code: FrequencyCode = "OTHER"
    # 服用時機。只在頻次代碼隱含「一日單一劑量」（目前僅 QD）且值為
    # `bedtime` 時才會影響時段判定；`before_meal`／`after_meal`／
    # `empty_stomach` 描述的是與進食的關係，不指向任何特定時段，
    # 一律不影響映射。見 `prescription_scan_service._resolve_slots`。
    timing: Optional[DrugTiming] = None
    indication: Optional[str] = None
    # 療程天數。有值時用來換算 Medication.end_date，讓療程結束後這顆藥
    # 自然從 find_active_by_ids 掉出去，不需要使用者手動停用。沒有值
    # （慢性病長期用藥是常態）就不設 end_date，維持長期有效。
    duration_days: Optional[int] = None
    # 使用者可覆寫頻次映射出的時段；OTHER 頻次且未指定時必須拒絕提交。
    slots: Optional[list[MedicationSlotType]] = None
    # 使用者可以把辨識出但不需要的項目（誤判、重複、不想建立）取消勾選。
    include: bool = True


class CommitPrescriptionDraftRequest(BaseModel):
    """提交草稿的請求本體。draft_id 由路由的路徑參數提供，不重複放在這裡。"""

    user_id: str
    drugs: list[CommitDrugItem] = Field(default_factory=list)


class PrescriptionCommitResult(BaseModel):
    """提交後的結果。

    medication_ids 涵蓋本次建立的所有藥品，PRN 也在其中——它們確實被建立了，
    只是不會出現在任何提醒的關聯裡。prn_medication_ids 是其中屬於 PRN 的子集，
    讓呼叫端不必重新猜測哪些藥沒有對應的提醒。

    reminder_ids 是這次提交實際建立或連結到的提醒規則 id（去重後）。沒有它，
    呼叫端只知道「藥品建立成功」，卻無從得知這顆藥有沒有真的掛上一筆排程器
    會挑中的提醒——「已建立」的回應不能是黑箱。冪等重放（草稿已被提交過、
    這次沒有取得提交權）時無法可靠回推當初建立的是哪些提醒，此欄位為空；
    這與 prn_medication_ids 在同一情境下的處理方式一致，見該欄位重放分支的
    說明。

    reactivated_slots 是這次提交把哪些時段從「停用／已過期／還沒到
    start_date」重新變回可排程狀態（見 find_or_create_reminder）。一個時段
    永遠只有一份規則，命中一筆原本關閉的規則時會直接復活它，連帶恢復掛在
    它底下、使用者當初就是要停掉的其他藥——這件事不能只讓資料庫默默發生，
    呼叫端（LIFF）要能據此在核對畫面事先揭露、送出後的訊息也如實反映，
    而不是讓使用者事後才在提醒列表發現多了一則自己沒印象重新開啟的提醒。
    冪等重放時同樣無法可靠回推，此欄位為空。
    """

    medication_ids: list[str] = Field(default_factory=list)
    prn_medication_ids: list[str] = Field(default_factory=list)
    reminder_ids: list[str] = Field(default_factory=list)
    reactivated_slots: list[MedicationSlotType] = Field(default_factory=list)
