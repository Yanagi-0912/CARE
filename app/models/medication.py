import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import ClassVar, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# 24 小時制 HH:MM
HHMM_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

MedicationSlotType = Literal["morning", "noon", "evening", "bedtime"]
# `cancelled` 是規則被使用者主動改動時，當日已展開但還沒確認的紀錄會落到的
# 狀態：關閉該時段，或把它改到別的時刻（改時段／改提醒時間，見
# `MedicationLogRepository.resync_pending_by_reminder`）——兩種情形下那筆紀錄
# 對應的排程都已經不存在了。
# 它與 `missed` 分開的理由：missed 代表「該吃卻沒吃」，會連帶發出家屬逾時警報、
# 也會進錯過時段的彙整通知；規則被主動改掉的那一次不該算在使用者頭上。留下
# 紀錄而不是直接刪除，是為了保住「這個時段當天確實展開過」這件事實，避免排程器
# 在同一天的後續 tick 又把它重新 upsert 回 pending。
#
# 它不是終局狀態：使用者若其實已服藥（先吃了藥才關掉時段），按下【我已用藥】
# 仍可轉成 `taken`——使用者按下的確認一律優先於系統推得的狀態。但它不會出現在
# 用藥歷史裡，那是內部記帳，不是使用者做過的事。
MedicationLogStatus = Literal["pending", "taken", "missed", "cancelled"]

# 醫囑頻次。無法明確歸類者一律 OTHER——臆測頻次會直接變成錯誤的服藥時間。
# 定義在這裡而非 prescription.py，是因為 prescription.py 需要 MedicationSlotType，
# 反向 import 會造成循環。
MedicationFrequencyCode = Literal["QD", "BID", "TID", "QID", "HS", "PRN", "OTHER"]

MedicationSource = Literal["manual", "prescription_ocr"]

DEFAULT_SLOT_TIMES: dict[str, str] = {
    "morning": "08:00",
    "noon": "12:00",
    "evening": "18:00",
    "bedtime": "21:30",
}

# 錯過多久之後就不再補推播。對應 APScheduler 的 misfire_grace_time。
# 預設取 20 分鐘（＝T+20 催促的門檻）：短暫部署造成的延遲仍會正常送達，
# 超過這個範圍代表整條 T+0／T+20／T+30 時序已經失去意義，補推只會變成連環轟炸。
#
# 放在模型層是因為有兩個消費者，而且它們必須用同一個值：`MedicationScheduler`
# 用它判斷展開出來的時段算不算錯過，`MedicationService` 用它判斷「改排程到已經
# 過去的時刻」要不要先把該時刻註銷掉（見 `update_reminder`）。兩邊一旦分岔，
# 服務層會擋掉排程器其實還會正常推播的時段，或反過來漏擋。
DEFAULT_MISFIRE_GRACE_MINUTES = 20

SLOT_DISPLAY_NAMES: dict[str, str] = {
    "morning": "早",
    "noon": "中",
    "evening": "晚",
    "bedtime": "睡前",
}


def _today_date_str() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


def ensure_aware_utc(dt: datetime) -> datetime:
    """
    將 naive datetime 視為 UTC 並補上時區。

    Motor client 未啟用 tz_aware，pymongo 會把 datetime 以 UTC 寫入、
    再以 naive UTC 讀回。任何從資料庫取回的時間都必須先經過這裡，
    才能安全地與帶時區的時間比較或做時區轉換。
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def to_taipei_hm(dt: Optional[datetime], default: str = "") -> str:
    """
    把時間格式化成台北時間的 HH:MM，供推播文案顯示。

    直接對資料庫取回的值呼叫 strftime 會顯示 UTC 時刻（與台北差 8 小時），
    所以一律先 ensure_aware_utc 再轉換。
    """
    if dt is None:
        return default
    return ensure_aware_utc(dt).astimezone(TAIPEI_TZ).strftime("%H:%M")


class MedicationReminder(BaseModel):
    """用藥提醒設定規則"""

    # 允許使用欄位名稱 (id) 或別名 (_id) 進行初始化，支援 MongoDB Document 與 Python 物件存取
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    creator_user_id: str                   # 開立提醒者 (家屬) LINE userId
    user_id: str                           # 服用藥物的使用者 LINE userId
    slot_type: MedicationSlotType
    scheduled_time: str = "08:00"
    start_date: str = Field(default_factory=_today_date_str)
    end_date: Optional[str] = None
    enabled: bool = True
    # 該時段應服用的藥品。純關聯欄位，排程器的展開與搶佔判定不讀它——
    # 那些併發行為已有既定條文與保證，不讓藥品關聯成為它們的輸入。
    # 本欄位之前寫入的規則沒有這個 key，讀回時為空陣列，行為與過去一致。
    medication_ids: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Medication(BaseModel):
    """一種藥。與時段規則分開存放，因此可以單獨停用或結束療程，
    而不影響同一時段的其他藥。"""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    user_id: str                           # 服用此藥的使用者 LINE userId
    created_by_user_id: str                # 建立者 LINE userId
    name: str
    generic_name: Optional[str] = None
    license_number: Optional[str] = None
    # 外觀欄位：形狀／顏色／刻痕／標註／外觀尺寸。license_number 是使用者從
    # 候選清單挑定的值時，於提交當下原樣帶自對應候選（DrugCandidate，見
    # prescription.py）；未挑選或無外觀記錄則留空字串，不是 None——與
    # DrugCatalogEntry 同一慣例，呼叫端（Flex 訊息、LIFF 清單）不必先判斷
    # 型別就能安全串接顯示。
    shape: str = ""
    color: str = ""
    score_line: str = ""
    mark_one: str = ""
    mark_two: str = ""
    size: str = ""
    # 縮圖的對外 URL。刻意不落地存進資料庫（欄位在寫入時永遠是 None）——
    # 縮圖檔案存不存在只有讀取當下才知道，見 MedicationService
    # get_user_reminders_with_medications 用 resolve_drug_appearance_image_url
    # 就地解析並以 model_copy 覆寫這個欄位，與 medication_scheduler
    # 的 _resolve_thumbnail 走同一條規則。查無縮圖或 license_number 未確定
    # 時為 None，呈現面據此安全地退回純文字（spec「照片缺席時的降級」）。
    thumbnail_url: Optional[str] = None
    # 食藥署仿單的適應症。與 thumbnail_url 同一慣例：欄位在寫入時永遠是 None，
    # 由 MedicationService 於讀取當下依 license_number 就地解析並以 model_copy
    # 覆寫——仿單資料是建置期產出的靜態檔，跟著藥品文件一起落地只會讓同一份
    # 內容在資料庫裡複製上萬次，且更新資料集時全部過期。
    #
    # 兩個欄位都給前端：`spc_indication_summary` 是給長輩看的濃縮版（可能為
    # None——不需要摘要或產不出合格摘要時），`spc_indication` 是食藥署原文，
    # 供展開對照。摘要缺席時前端顯示原文，這是 spec 的「摘要缺席時的降級」。
    #
    # 證號未確定時兩者皆為 None：不知道是哪一張藥證，顯示的適應症就可能屬於
    # 另一顆藥——與「證號不確定時不得顯示藥丸照片」同一條安全邊界。
    #
    # **這兩個欄位 SHALL NOT 進入任何推播訊息**：仿單涵蓋該藥證的全部核准
    # 適應症，揭露範圍比藥袋上那一行更大。
    spc_indication: Optional[str] = None
    spc_indication_summary: Optional[str] = None
    unit_content: Optional[str] = None
    total_quantity: Optional[int] = None
    usage_raw: Optional[str] = None        # 藥袋上的用法原文，供使用者核對
    frequency_code: MedicationFrequencyCode = "OTHER"
    # 適應症會直接揭露病情，僅供 LIFF 內呈現，不得進入任何推播訊息。
    indication: Optional[str] = None
    source: MedicationSource = "manual"
    start_date: str = Field(default_factory=_today_date_str)
    end_date: Optional[str] = None
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MedicationLog(BaseModel):
    """用藥執行與催促/警報日誌"""

    # 允許使用欄位名稱 (id) 或別名 (_id) 進行初始化，支援 MongoDB Document 與 Python 物件存取
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")

    reminder_id: str
    user_id: str                           # 服用藥物的使用者 LINE userId
    alert_notify_user_id: str              # 逾時未用藥通報對象 (家屬) LINE userId
    slot_type: MedicationSlotType
    scheduled_at: datetime
    timeout_at: datetime
    status: MedicationLogStatus = "pending"
    taken_at: Optional[datetime] = None
    patient_reminder_sent: bool = False
    urgent_reminder_sent: bool = False
    caregiver_alert_sent: bool = False
    # 三個階段各自的推播嘗試次數，由 `release_*` 在推播失敗時累加（見
    # `MedicationLogRepository` 的「推播重試上限」段落）。分成三個欄位而不是
    # 一個總數：一個階段耗盡預算不該連帶剝奪後兩個階段的重試機會——T+0 送不出
    # 去（例如當下網路瞬斷）與 T+30 家屬警報送不出去是兩件獨立的事。
    #
    # 本欄位之前寫入的紀錄沒有這些 key，讀回時為 0，與過去行為一致；
    # 資料庫端則由 `$inc` 自行建立欄位，不需要回填。
    patient_reminder_attempts: int = 0
    urgent_reminder_attempts: int = 0
    caregiver_alert_attempts: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CreateMedicationReminderRequest(BaseModel):
    user_id: str                           # 服用藥物的使用者 LINE userId
    slots: List[MedicationSlotType]
    # 各時段的自訂提醒時間（例如 {"morning": "08:30"}）。key 限定合法時段，
    # 未指定的時段沿用 DEFAULT_SLOT_TIMES。
    slot_times: Optional[dict[MedicationSlotType, str]] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    @field_validator("slot_times")
    @classmethod
    def _validate_slot_times(
        cls, value: Optional[dict[str, str]]
    ) -> Optional[dict[str, str]]:
        """
        擋掉格式錯誤的時間。若讓 "9am" 這種值寫進資料庫，排程器的 strptime
        會拋錯並被 except 吞掉 —— 該筆提醒將永遠不會觸發，且沒有任何錯誤回饋。
        """
        if value is None:
            return value
        for slot, time_str in value.items():
            if not HHMM_PATTERN.match(time_str):
                raise ValueError(
                    f"時段 {slot} 的時間格式須為 HH:MM（24 小時制），收到 {time_str!r}"
                )
        return value


class UpdateMedicationReminderRequest(BaseModel):
    """PUT /reminders/{id} 的請求。

    這裡的 Optional 一律是「可以不帶」，**不是**「可以是 null」。服務層以
    `model_fields_set` 區分兩者：沒帶的欄位不會出現在 update_data 裡，
    有帶且是 null 的只接受 `end_date`（把療程改回長期的唯一途徑），其餘欄位
    的 null 一律 400。理由見 `MedicationService.update_reminder`——
    `scheduled_time` 若被寫成 null，排程器的 strptime 會拋錯並被 except 吞掉，
    那筆提醒從此永遠不會觸發，且沒有任何錯誤回饋。
    """

    slot_type: Optional[MedicationSlotType] = None
    scheduled_time: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    enabled: Optional[bool] = None

    # 只有這個欄位的 null 有意義：null = 沒有結束日期 = 長期服用。
    NULLABLE_FIELDS: ClassVar[frozenset[str]] = frozenset({"end_date"})

    @field_validator("scheduled_time")
    @classmethod
    def _validate_scheduled_time(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not HHMM_PATTERN.match(value):
            raise ValueError(
                f"提醒時間格式須為 HH:MM（24 小時制），收到 {value!r}"
            )
        return value



class MedicationReminderResponse(BaseModel):
    reminder: MedicationReminder


class MedicationLogResponse(BaseModel):
    log: MedicationLog


class MedicationReminderWithMedications(MedicationReminder):
    """GET /reminders 的回應形狀：在提醒規則本體之外附上已解析好的藥品清單。

    `medication_ids` 只是關聯 id，LIFF 要顯示藥名（尤其是藥袋辨識建立的藥）
    不能只靠這個欄位；把解析放在這裡而不是要求前端逐一查詢每個 id，
    省掉 N 次額外的往返。"""

    medications: List[Medication] = Field(default_factory=list)
