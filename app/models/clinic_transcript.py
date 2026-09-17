"""看診錄音紀錄的資料模型。

### 音檔不存，只存文字

`ClinicVisitRecord` 裡沒有任何音檔欄位，也沒有音檔路徑。轉錄完就刪，理由有兩個：
診間錄音的敏感度比一般對話高，留著沒有用途只有風險；而且家人真正要看的是
可以搜尋、可以引用的文字，不是一段要從頭聽到尾的錄音。

### 保存 30 天，跟對話原文同一條線

`expires_at` 由 Mongo 的 TTL 索引執行。30 天不是技術限制，是跟既有的對話原文
保存期限對齊——同一個使用者的兩種紀錄用不同的期限，對他來說無法解釋。

### `consent` 是功能的一部分，不是稽核欄位

衛福部《醫療機構醫療隱私維護規範》第二點：診療過程中，醫病任一方如需錄音或錄影，
均應先徵得對方之同意。所以錄音一定要走過徵詢這一步，而徵詢的結果會改變這份紀錄
是什麼東西：

- `doctor_agreed`：醫師同意了，錄的是診間對話。
- `self_recap`：醫師不同意或長輩不好意思問，改成出診間之後自己複述。
  內容一樣有價值，但它是「長輩記得的版本」而不是「醫師講的版本」，
  畫面上要講清楚，摘要的可信度也不同。

沒有第三種。沒有徵詢就不會有紀錄。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# 跟對話原文一致（見模組開頭）。
RETENTION = timedelta(days=30)

# 錄的是診間對話，還是出診間後長輩自己複述。
ConsentMode = Literal["doctor_agreed", "self_recap"]

# 轉錄是背景工作，不在上傳那個請求裡做完。十分鐘的門診錄音跑多久沒有實測數字，
# 但一定遠超過一個 HTTP 請求該撐住的時間；手機在等待中鎖屏、切到別的 app、
# 走出醫院掉訊號都是常態。上傳完就回一筆 `processing` 的紀錄，轉好了再推播通知。
#
# `failed` 是終局但不是死路：音檔已經刪了不能重跑，所以畫面要讓使用者知道這次沒了，
# 而不是留一個永遠轉圈的紀錄假裝還在處理。
RecordStatus = Literal["processing", "ready", "failed"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TranscriptSegment(BaseModel):
    """逐字稿的一段。

    刻意沒有 speaker 欄位——理由見 `app/services/speech/clinic_transcribe.py`
    的模組說明。段落只表示「這裡換人講了」，不表示換成了誰。
    """

    text: str
    start_seconds: Optional[float] = None


class MedicationChangeNote(BaseModel):
    description: str
    # 逐字稿裡的原文，照抄。沒有這個就不成立（見 summarizer 規則 4）。
    quote: str


class DrugHintNote(BaseModel):
    """逐字稿某處可能在講使用者清單上的某顆藥。

    `heard` 與 `medication_name` 兩個都存，因為我們不宣稱哪一個才對。
    """

    medication_name: str
    heard: str
    start: int
    score: float


class ClinicVisitSummaryModel(BaseModel):
    """五個固定欄位。欄位名稱一律避開說話者，理由見 summarizer。"""

    main_points: List[str] = Field(default_factory=list)
    medication_changes: List[MedicationChangeNote] = Field(default_factory=list)
    next_visit: str = ""
    reminders: List[str] = Field(default_factory=list)
    unclear: List[str] = Field(default_factory=list)
    truncated: bool = False


class ClinicVisitRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    # 就診者。家人代錄時 user_id 是長輩、created_by_user_id 是家人。
    user_id: str
    created_by_user_id: str
    # 這次錄音對應哪一筆掛號提醒。沒有從掛號進來時為 None（例如臨時掛號）。
    appointment_id: Optional[str] = None
    hospital_name: str = ""
    department: str = ""

    consent: ConsentMode
    status: RecordStatus = "processing"
    # `status == "failed"` 時給使用者看的一句話。不放技術細節：使用者能做的事
    # 只有「知道這次沒了」和「下次再錄」。
    failure_reason: str = ""
    recorded_at: datetime = Field(default_factory=_now)
    created_at: datetime = Field(default_factory=_now)
    expires_at: datetime = Field(default_factory=lambda: _now() + RETENTION)

    segments: List[TranscriptSegment] = Field(default_factory=list)
    summary: ClinicVisitSummaryModel = Field(default_factory=ClinicVisitSummaryModel)
    drug_hints: List[DrugHintNote] = Field(default_factory=list)
    # 只進紀錄不上畫面：用來事後回答「診間到底幾個人在講」這種只能靠實際資料
    # 回答的問題，也是日後判斷語者分離值不值得繼續開的依據。
    speaker_count: int = 0

    @property
    def transcript_text(self) -> str:
        return "\n".join(segment.text for segment in self.segments)
