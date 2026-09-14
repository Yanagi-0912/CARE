import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import ClassVar, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
# 預設取 20 分鐘（＝預設的催促時機 URGENT_AFTER_ANCHOR_MINUTES）：短暫部署造成
# 的延遲仍會正常送達，超過這個範圍代表整條 T+0／T+20／T+30 時序已經失去意義，
# 補推只會變成連環轟炸。「寬限＝催促時機」這個關係保證晚送的 T+0 不會在同一輪
# 緊接著催促；用藥提醒拉霸挑的 +10／+15 會打破它，所以拉霸只對準時送出的 T+0
# 套用催促時機（見 MedicationScheduler._reminder_tone）。
#
# 放在模型層是因為有兩個消費者，而且它們必須用同一個值：`MedicationScheduler`
# 用它判斷展開出來的時段算不算錯過，`MedicationService` 用它判斷「改排程到已經
# 過去的時刻」要不要先把該時刻註銷掉（見 `update_reminder`）。兩邊一旦分岔，
# 服務層會擋掉排程器其實還會正常推播的時段，或反過來漏擋。
DEFAULT_MISFIRE_GRACE_MINUTES = 20

# T+20 催促與 T+30 家屬通報距離最晚服藥時刻（timeout_anchor_time）的分鐘數。
# 排程器展開紀錄、服務層在長輩改提醒設定時對齊紀錄，都用這兩個值；拉霸改寫催促
# 時間時，也靠 CAREGIVER_ALERT_AFTER_ANCHOR_MINUTES 從 timeout_at 反推最晚服藥
# 時刻。放在模型層的理由同上：幾個使用端必須同值。
URGENT_AFTER_ANCHOR_MINUTES = 20
CAREGIVER_ALERT_AFTER_ANCHOR_MINUTES = 30

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


# 飯前／飯後／無關聯三種服藥時機。推播分區與排序一律依這個順序，不是條目
# 在陣列裡的原始輸入順序——同一規則裡誰先展開、家屬彙整通知裡誰先列，
# 全部只看 MEAL_TIMING_ORDER。
MealTiming = Literal["before_meal", "after_meal", "none"]
MEAL_TIMING_ORDER: tuple[str, ...] = ("before_meal", "after_meal", "none")


class ReminderEntry(BaseModel):
    """一筆時段規則裡的一個服藥條目：同一種服藥時機、同一個提醒時刻、
    同一批藥。一個時段規則可以有多個條目（例如「早」拆成飯前 07:30 與
    飯後 08:30），但同一種 meal_timing 至多一個——飯前藥不會分兩批推播，
    要嘛合併成一個條目，要嘛其中一批其實屬於別的時機（見 validate_entries）。
    """

    meal_timing: MealTiming = "none"
    scheduled_time: str
    medication_ids: List[str] = Field(default_factory=list)

    @field_validator("scheduled_time")
    @classmethod
    def _validate_scheduled_time(cls, value: str) -> str:
        if not HHMM_PATTERN.match(value):
            raise ValueError(
                f"條目時間格式須為 HH:MM（24 小時制），收到 {value!r}"
            )
        return value


class ReminderEntryInput(ReminderEntry):
    """API 輸入用的條目形狀。欄位與 ReminderEntry 完全相同，獨立命名只是
    讓請求模型與內部儲存模型的型別簽章分開，避免日後兩邊需求分岔時
    互相牽制（目前刻意不加任何額外欄位或限制，YAGNI）。"""


def validate_entries(entries: list[ReminderEntry]) -> list[ReminderEntry]:
    """
    條目層級的不變量檢查，供 `MedicationReminder` 與各請求模型共用：

    - 至少一個條目——沒有條目等於這個時段沒有規則，不該存在一筆空規則。
    - `meal_timing` 不得重複——同一時機拆成兩個條目，推播分區與逾時判定
      會對不上是哪一個條目。
    - 同一顆藥不得出現在兩個條目——一顆藥屬於哪個時機是唯一的，出現在
      兩邊會讓「這個時段的藥是否都已確認」失去單一答案。

    回傳依 `MEAL_TIMING_ORDER` 排序過的新 list，不修改輸入 list 本身。
    """
    if not entries:
        raise ValueError("規則至少須有一個服藥條目")

    seen_timings: set[str] = set()
    seen_medication_ids: set[str] = set()
    for entry in entries:
        if entry.meal_timing in seen_timings:
            raise ValueError(f"服藥時機「{entry.meal_timing}」重複")
        seen_timings.add(entry.meal_timing)

        for medication_id in entry.medication_ids:
            if medication_id in seen_medication_ids:
                raise ValueError(f"藥品 {medication_id} 不得同時出現在兩個條目")
            seen_medication_ids.add(medication_id)

    return sorted(entries, key=lambda entry: MEAL_TIMING_ORDER.index(entry.meal_timing))


def derive_entry_fields(entries: list[ReminderEntry]) -> dict:
    """
    由條目重算規則的三個派生欄位。所有寫入路徑（service、repository）
    SHALL 只用這個函式算派生值，SHALL NOT 自行重算——三者（最早時刻／
    最晚時刻／藥品聯集）的定義只此一份，散落在多處會在日後修改時分岔。

    內部會先呼叫 `validate_entries`，所以呼叫端不必先自行驗證再呼叫。
    `entries` 鍵回傳的是 `model_dump()` 過的 plain dict、依 MEAL_TIMING_ORDER
    排序，讓呼叫端可以直接放進 Mongo 的 `$set`。
    """
    ordered = validate_entries(entries)

    medication_ids: list[str] = []
    for entry in ordered:
        for medication_id in entry.medication_ids:
            if medication_id not in medication_ids:
                medication_ids.append(medication_id)

    return {
        "entries": [entry.model_dump() for entry in ordered],
        "scheduled_time": min(entry.scheduled_time for entry in ordered),
        "timeout_anchor_time": max(entry.scheduled_time for entry in ordered),
        "medication_ids": medication_ids,
    }


class MedicationReminder(BaseModel):
    """用藥提醒設定規則"""

    # 允許使用欄位名稱 (id) 或別名 (_id) 進行初始化，支援 MongoDB Document 與 Python 物件存取
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    creator_user_id: str                   # 開立提醒者 (家屬) LINE userId
    user_id: str                           # 服用藥物的使用者 LINE userId
    slot_type: MedicationSlotType
    scheduled_time: str = "08:00"
    # 條目中最晚的時刻，決定該規則整體逾時（T+30）的錨點——飯前飯後拆成
    # 兩批藥時，逾時警報不該用最早那批的時間去算，否則後一批藥還沒到時間
    # 就先被判定逾時。舊規則沒有多個條目，讀回後與 scheduled_time 相等
    # （見下方 mode="before" 合成邏輯），行為與本變更前完全一致。
    timeout_anchor_time: str = ""
    start_date: str = Field(default_factory=_today_date_str)
    end_date: Optional[str] = None
    enabled: bool = True
    # 該時段應服用的藥品。純關聯欄位，排程器的展開與搶佔判定不讀它——
    # 那些併發行為已有既定條文與保證，不讓藥品關聯成為它們的輸入。
    # 本欄位之前寫入的規則沒有這個 key，讀回時為空陣列，行為與過去一致。
    medication_ids: List[str] = Field(default_factory=list)
    # 飯前／飯後／無關聯的服藥條目。scheduled_time／timeout_anchor_time／
    # medication_ids 三者都是由 entries 算出來的派生值（見 derive_entry_fields），
    # 不是各自獨立輸入——mode="after" 會在每次建構模型時強制以 entries
    # 重新覆寫這三者，就算呼叫端直接塞了不一致的值也一樣。
    entries: List[ReminderEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def _synthesize_entries_from_legacy_fields(cls, data):
        """
        既有規則（本變更前寫入）的文件沒有 `entries` 欄位；讀回時在這裡
        合成一個單一 `none` 條目，讓下面 mode="after" 的
        `validate_entries`／`derive_entry_fields` 可以一視同仁地處理新舊
        資料，不必在每個讀取路徑各自判斷「這筆有沒有 entries」（spec
        「既有規則於資料庫中沒有 entries 時」）。

        只在 `entries` 缺席或空時合成，且只讀取 `dict` 形式的輸入——
        model_copy() 等場景傳進來的已經是驗證過的物件，不需要、也不應該
        重新合成。
        """
        if not isinstance(data, dict):
            return data
        if data.get("entries"):
            return data

        data = dict(data)
        data["entries"] = [
            {
                "meal_timing": "none",
                "scheduled_time": data.get("scheduled_time") or "08:00",
                "medication_ids": data.get("medication_ids") or [],
            }
        ]
        return data

    @model_validator(mode="after")
    def _validate_and_derive_entry_fields(self) -> "MedicationReminder":
        """
        以 entries 為唯一真相，驗證後覆寫三個派生欄位——SHALL NOT 由 API
        直接寫入（spec「規則內的服藥條目」）。就算呼叫端在建構時另外傳了
        scheduled_time／timeout_anchor_time／medication_ids，這裡都會用
        entries 算出來的值蓋掉，兩者不可能不一致。
        """
        self.entries = validate_entries(self.entries)
        derived = derive_entry_fields(self.entries)
        self.scheduled_time = derived["scheduled_time"]
        self.timeout_anchor_time = derived["timeout_anchor_time"]
        self.medication_ids = derived["medication_ids"]
        return self


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
    # 調劑機構與調劑日期，原樣帶自藥袋辨識結果（`RecognitionResult`）。
    #
    # 這兩欄在同一次 OCR 就已經讀出來了，先前只是沒有落地——藥袋是「這個人
    # 去哪裡看診、什麼時候拿的藥」的唯一來源，而健保雲端藥歷看不到自費看診
    # （不插健保卡就沒有就醫紀錄），所以這裡是那件事唯一的入口。
    #
    # `institution` 的授權分級是 **SENSITIVE 而非 GENERAL**（見
    # `family_authorization.CLASSIFICATION`）：「常去腫瘤科」「上個月去了
    # 身心科」揭露的病情遠多於藥名，與 `indication` 同一條理由。
    #
    # `dispensed_date` 是藥袋上印的調劑日期（YYYY-MM-DD），**不是掃描時間**。
    # 看診紀錄要以它分組：長輩可能拖三天才掃，`created_at` 不是看診日。
    #
    # 手動新增的藥、以及本欄位之前寫入的紀錄都是 None，呈現面據此顯示「未
    # 記錄來源」而不是留白。
    institution: Optional[str] = None
    dispensed_date: Optional[str] = None
    # 建立這筆藥的那一次掃描（`PrescriptionDraft.draft_id`）。草稿本身有 TTL
    # 會過期，過期後就再也無從得知「這幾種藥是同一次掃進來的」——所以要在
    # 這裡留一份。
    #
    # 它與 `(institution, dispensed_date)` 分組是兩件事：實測同一個藥袋在
    # 42 分鐘內被掃了三次，產生三個不同的 draft_id 但只有一次看診。看診紀錄
    # 用機構＋日期分組，draft_id 用來回答「這次掃描進了哪些藥」。
    draft_id: Optional[str] = None
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
    # 建立這條提醒的人（取自 reminder.creator_user_id）。本欄位曾是 T+30 逾時通報
    # 的唯一收件人；現在收件人改由家庭授權的通知政策決定（medication_missed），
    # 這裡只留作紀錄，推播路徑不再讀它。
    alert_notify_user_id: str
    slot_type: MedicationSlotType
    scheduled_at: datetime
    timeout_at: datetime
    status: MedicationLogStatus = "pending"
    taken_at: Optional[datetime] = None
    # T+20 二次催促的送出時刻，也是排程器挑出「該催促了」的依據（見
    # MedicationLogRepository.list_pending_urgent_reminders）。展開時預設為最晚
    # 服藥時刻＋20 分鐘，送 T+0 提醒時由拉霸改寫成＋nudge_minutes 分鐘。本欄位
    # 之前寫入的紀錄沒有這個 key，讀回時為 None，查詢端有退回分支。
    urgent_at: Optional[datetime] = None
    # 用藥提醒拉霸在送 T+0 時挑的選項（見 app/services/medication/reminder_variants.py）。
    # None 代表這一頓沒有進拉霸：本功能上線前的紀錄、長輩關掉提醒而沒送出的那頓、
    # 或拉霸出錯而退回現行版本的那頓。`nudge_minutes` 另外會在長輩當天改了提醒
    # 設定、催促時間被重設時清成 None——那一頓的催促時機已經不是拉霸挑的。
    reminder_tone: Optional[str] = None
    nudge_minutes: Optional[int] = None
    # 逐藥確認累積的藥品 id 集合（見 spec「逐藥確認」）。集合語意由服務層
    # 保證冪等，這裡只是純粹的儲存欄位。本欄位之前寫入的紀錄沒有這個 key，
    # 讀回時為空陣列，不影響既有的整批確認行為。
    taken_medication_ids: List[str] = Field(default_factory=list)
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
    # 各時段的飯前／飯後條目（例如 {"morning": [{飯前 07:30}, {飯後 08:30}]}）。
    # 與 slot_times 是兩套獨立的輸入：帶了某時段的 slot_entries 就以條目為準，
    # 該時段的 scheduled_time／timeout_anchor_time／medication_ids 全部由
    # derive_entry_fields 重算，slot_times 對該時段不再生效。
    slot_entries: Optional[dict[MedicationSlotType, List[ReminderEntryInput]]] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    @field_validator("slot_entries")
    @classmethod
    def _validate_slot_entries(
        cls, value: Optional[dict[str, List[ReminderEntryInput]]]
    ) -> Optional[dict[str, List[ReminderEntryInput]]]:
        """
        對每個時段各自跑一次 validate_entries——時段之間彼此獨立，
        「早」的條目重複不該連帶擋掉「中」的合法輸入。
        """
        if value is None:
            return value
        for entries in value.values():
            validate_entries(entries)
        return value

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
    # 整批覆寫這個時段的服藥條目。帶了就以條目為準重算三個派生欄位；
    # 未帶（None）代表這次更新不動條目，沿用既有的 entries——None 不在
    # NULLABLE_FIELDS 裡，代表「明確傳 null」與「沒帶這個欄位」不同義，
    # 一律當成請求格式錯誤擋在服務層之前，理由同 scheduled_time：條目
    # 若被寫成 null，該規則會失去唯一真相，排程與逐藥確認都無所依據。
    entries: Optional[List[ReminderEntryInput]] = None
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

    @field_validator("entries")
    @classmethod
    def _validate_entries(
        cls, value: Optional[List[ReminderEntryInput]]
    ) -> Optional[List[ReminderEntryInput]]:
        if value is not None:
            validate_entries(value)
        return value


class CreateMedicationRequest(BaseModel):
    """以藥名手動新增藥品的請求（spec「藥品的列出與手動新增」）。

    只收 user_id 與 name——手動新增的藥不含藥證、外觀與適應症，那些欄位
    只有藥袋辨識（prescription_ocr）才會有資料來源，手動新增硬要收反而是
    邀請使用者填進不可信的內容。
    """

    user_id: str
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _validate_name_not_blank_after_strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("藥名不得為空白")
        return stripped


class MedicationReminderResponse(BaseModel):
    reminder: MedicationReminder


class MedicationLogResponse(BaseModel):
    log: MedicationLog


class MedicationVisit(BaseModel):
    """一次看診：同一個機構、同一個調劑日期拿到的那些藥。

    以 `(institution, dispensed_date)` 分組而不是以掃描分組——實測同一個藥袋
    在 42 分鐘內被掃了三次，按掃描列會讓同一家醫院出現三次。`scan_count` 保留
    那個事實供除錯，但不是分組依據。

    `institution` 為 None 代表「未記錄來源」：手動新增的藥，以及本欄位落地
    之前建立的舊紀錄。呈現面 SHALL 明確標示，不要留白。
    """

    institution: Optional[str] = None
    dispensed_date: Optional[str] = None
    medication_ids: List[str] = Field(default_factory=list)
    medication_names: List[str] = Field(default_factory=list)
    scan_count: int = 0
    first_created_at: Optional[datetime] = None


class MedicationVisitsResponse(BaseModel):
    visits: List[MedicationVisit] = Field(default_factory=list)


class MedicationReminderWithMedications(MedicationReminder):
    """GET /reminders 的回應形狀：在提醒規則本體之外附上已解析好的藥品清單。

    `medication_ids` 只是關聯 id，LIFF 要顯示藥名（尤其是藥袋辨識建立的藥）
    不能只靠這個欄位；把解析放在這裡而不是要求前端逐一查詢每個 id，
    省掉 N 次額外的往返。"""

    medications: List[Medication] = Field(default_factory=list)
