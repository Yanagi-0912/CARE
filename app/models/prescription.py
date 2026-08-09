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

    欄位只涵蓋 Medication 真正會寫入的部分——timing／duration_days 這類
    只在辨識階段供人核對用的顯示欄位，提交後就沒有去處，不放進來。
    """

    name: str
    generic_name: Optional[str] = None
    # 前端把掃描時藥證庫比對到的證號原樣回傳；提交階段不重新比對，
    # 因為使用者可能已把藥名編輯成別的字串，那應該是下一次掃描的責任，
    # 不該在提交當下悄悄用新名字重新配對出不同的證號。
    license_number: Optional[str] = None
    unit_content: Optional[str] = None
    total_quantity: Optional[int] = None
    usage_raw: Optional[str] = None
    frequency_code: FrequencyCode = "OTHER"
    indication: Optional[str] = None
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
    """

    medication_ids: list[str] = Field(default_factory=list)
    prn_medication_ids: list[str] = Field(default_factory=list)
