"""掛號提醒的資料模型。

與用藥提醒（`app/models/medication.py`）同構，但有兩個本質差異決定了這裡的形狀：

1. **單次事件。** 用藥是「規則 + 每日展開的 log」兩層；掛號只會發生一次，規則
   本身就是那唯一的一次。三個推播階段的旗標因此直接放在同一份文件上，不另開一個
   log collection——那一層在單次事件上只會多出「規則與 log 不同步」這一種失敗方式。
2. **時間是絕對的時間點。** `appointment_at` 是 timezone-aware 的瞬間，T-1h／T+0／
   T+30 都是對它加減固定的時間長度，本身與時區無關；真正需要「當地時間」的只有
   兩件事——「當日結束」落在哪一刻，以及推播文案要顯示幾點。那個當地時間取自使用者
   送來的 offset（`appointment_utc_offset_minutes`），不是伺服器時區，也不是 UTC。
"""

from datetime import datetime, time, timedelta, timezone
from typing import ClassVar, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.medication import ensure_aware_utc

AppointmentStatus = Literal["scheduled", "departed", "attended", "missed", "cancelled"]

# 仍在等待到診的狀態。三個推播階段只挑這兩種；到診、錯過、取消都是終局。
OPEN_STATUSES: tuple[str, ...] = ("scheduled", "departed")

# 提醒節奏（已拍板，使用者不能自訂）。
PRE_REMINDER_LEAD = timedelta(hours=1)  # T-1h：「我已出發」
CAREGIVER_ALERT_DELAY = timedelta(minutes=30)  # T+30：家屬警報

# 「當日結束」最早不得早於門診後 60 分鐘。
#
# 當日結束的本意是當地的午夜，但 23:45 的門診若照字面在 00:00 標記 missed，
# T+30 的家屬警報（00:15）永遠沒有機會發出——兩條已拍板的規則在深夜門診上互相
# 矛盾。取 T+60 讓 T+30 至少有 30 分鐘的窗口（足夠推播重試上限的 5 輪），
# 對一般門診（午夜前一小時以前）完全不影響。
MIN_DAY_END_AFTER_APPOINTMENT = timedelta(minutes=60)

# 字串欄位的長度上限與錯誤訊息用的中文名稱。上限只為了擋掉明顯的誤用
# （貼上一整篇文章），不是業務規則。
TEXT_FIELD_LIMITS: dict[str, int] = {
    "facility_id": 64,
    "hospital_name": 100,
    "hospital_address": 200,
    "hospital_phone": 30,
    "department": 50,
    "doctor_name": 50,
    "serial_number": 20,
    "note": 500,
}
TEXT_FIELD_LABELS: dict[str, str] = {
    "facility_id": "院所代碼",
    "hospital_name": "醫院名稱",
    "hospital_address": "醫院地址",
    "hospital_phone": "醫院電話",
    "department": "科別",
    "doctor_name": "醫師",
    "serial_number": "看診號",
    "note": "備註",
    "appointment_at": "門診時間",
    "enabled": "提醒開關",
}
REQUIRED_TEXT_FIELDS: frozenset[str] = frozenset({"hospital_name", "department"})
NULLABLE_TEXT_FIELDS: frozenset[str] = frozenset(
    {
        "facility_id",
        "hospital_address",
        "hospital_phone",
        "doctor_name",
        "serial_number",
        "note",
    }
)


def fixed_offset(offset_minutes: int) -> timezone:
    return timezone(timedelta(minutes=offset_minutes))


def utc_offset_minutes(dt: datetime) -> int:
    """呼叫端必須先確認 `dt` 帶時區。"""
    offset = dt.utcoffset()
    assert offset is not None
    return int(offset.total_seconds() // 60)


def day_end_for(appointment_at: datetime, offset_minutes: int) -> datetime:
    """門診「當日結束」的瞬間（UTC）：當地午夜，但不早於門診後 60 分鐘。"""
    tz = fixed_offset(offset_minutes)
    local = appointment_at.astimezone(tz)
    midnight = datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=tz)
    return max(midnight, appointment_at + MIN_DAY_END_AFTER_APPOINTMENT).astimezone(
        timezone.utc
    )


def actionable_from(appointment_at: datetime, offset_minutes: int) -> datetime:
    """最早可以回報「出發／到診」的瞬間：門診當地日的 00:00，或 T-1h，取較早者。

    擋的是「一週前手滑按了我已到診」：到診會取消所有後續推播，那正是這個功能要
    防止的結果（錯過門診）。取較早者是為了凌晨的門診——00:30 的診，T-1h 的卡片
    在前一天 23:30 就送達，卡片上的按鈕必須按得下去。
    """
    tz = fixed_offset(offset_minutes)
    day_start = datetime.combine(
        appointment_at.astimezone(tz).date(), time.min, tzinfo=tz
    )
    return min(day_start, appointment_at - PRE_REMINDER_LEAD)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Motor 以 naive UTC 讀回時間；一律補上時區並統一成 UTC，比較才安全。"""
    if value is None:
        return None
    return ensure_aware_utc(value).astimezone(timezone.utc)


class AppointmentReminder(BaseModel):
    """資料庫中的一筆掛號提醒。

    API 回應**不直接使用這個模型**——三個推播階段的旗標與嘗試次數是排程器的內部
    記帳，時間也要換回使用者的 offset 才能輸出，見 `to_response`。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    user_id: str  # 就診者 LINE userId
    creator_user_id: str  # 建立者（可能是家屬）。來源紀錄，SHALL NOT 構成授權依據。

    # 門診時間，在記憶體中一律是 UTC aware；使用者送來的 offset 另存。
    appointment_at: datetime
    # 使用者送來的 UTC offset（分鐘，台灣為 480）。決定「當日結束」與推播顯示的
    # 當地時間，也決定 API 回傳時間字串的 offset——前端原樣送、原樣顯示。
    appointment_utc_offset_minutes: int
    # 「當日結束」的瞬間，建立／改時間時算好存下，排程器的查詢才能直接比大小。
    day_end_at: datetime

    facility_id: Optional[str] = None  # 院所查詢回傳的 id；可為 null，SHALL NOT 當外鍵
    hospital_name: str  # 權威顯示值
    hospital_address: Optional[str] = None
    hospital_phone: Optional[str] = None
    department: str
    doctor_name: Optional[str] = None
    serial_number: Optional[str] = None
    note: Optional[str] = None
    # 刻意不存病名、主訴、診斷：不存，就不會有人不小心把它塞進推播。

    status: AppointmentStatus = "scheduled"
    departed_at: Optional[datetime] = None
    departed_by_user_id: Optional[str] = None
    attended_at: Optional[datetime] = None
    attended_by_user_id: Optional[str] = None
    # 只管推播。關閉後三個階段都不送，但狀態機照走（仍可回報出發／到診，
    # 當日結束仍會標記 missed）。
    enabled: bool = True

    # ── 排程器內部記帳（不進 API 回應）──────────────────────────────
    # 三個階段各自的「已送出」旗標與嘗試次數，語意與 MedicationLog 相同：
    # 旗標是推播權的原子搶佔，嘗試次數是重試上限（見 app/repositories/push_claim.py）。
    pre_reminder_sent: bool = False  # T-1h
    start_reminder_sent: bool = False  # T+0
    caregiver_alert_sent: bool = False  # T+30
    pre_reminder_attempts: int = 0
    start_reminder_attempts: int = 0
    caregiver_alert_attempts: int = 0

    created_at: datetime
    updated_at: datetime

    @field_validator(
        "appointment_at",
        "day_end_at",
        "departed_at",
        "attended_at",
        "created_at",
        "updated_at",
    )
    @classmethod
    def _normalize_to_utc(cls, value: Optional[datetime]) -> Optional[datetime]:
        return _as_utc(value)

    @property
    def tz(self) -> timezone:
        return fixed_offset(self.appointment_utc_offset_minutes)

    def local(self, value: datetime) -> datetime:
        return value.astimezone(self.tz)

    @property
    def local_appointment_at(self) -> datetime:
        return self.local(self.appointment_at)

    def notify_at(self) -> List[datetime]:
        """後端排定的推播時間點，依序為 T-1h、T+0、T+30。

        固定是 3 筆或 0 筆：還會推播時回傳完整的三個時間點（包含已經送過的），
        不會再推播時（提醒已關閉，或狀態已是到診／錯過／取消）回傳空陣列。
        前端據此「有就顯示、空的就不顯示」，不需要自己判斷狀態或計算時間。
        """
        if not self.enabled or self.status not in OPEN_STATUSES:
            return []
        return [
            self.local(self.appointment_at - PRE_REMINDER_LEAD),
            self.local_appointment_at,
            self.local(self.appointment_at + CAREGIVER_ALERT_DELAY),
        ]

    def to_response(self) -> "AppointmentReminderResponse":
        def local_or_none(value: Optional[datetime]) -> Optional[datetime]:
            return None if value is None else self.local(value)

        return AppointmentReminderResponse(
            id=self.id or "",
            user_id=self.user_id,
            creator_user_id=self.creator_user_id,
            appointment_at=self.local_appointment_at,
            facility_id=self.facility_id,
            hospital_name=self.hospital_name,
            hospital_address=self.hospital_address,
            hospital_phone=self.hospital_phone,
            department=self.department,
            doctor_name=self.doctor_name,
            serial_number=self.serial_number,
            note=self.note,
            status=self.status,
            departed_at=local_or_none(self.departed_at),
            departed_by_user_id=self.departed_by_user_id,
            attended_at=local_or_none(self.attended_at),
            attended_by_user_id=self.attended_by_user_id,
            enabled=self.enabled,
            notify_at=self.notify_at(),
            created_at=self.local(self.created_at),
            updated_at=self.local(self.updated_at),
        )


class AppointmentReminderResponse(BaseModel):
    """API 回傳的掛號提醒。

    **每一個 key 永遠存在**，沒有值時是 `null`——回應裡「沒有這個 key」不會發生，
    前端不必區分兩者。所有時間都以 `appointment_at` 的 offset 輸出（見
    `AppointmentReminder.to_response`）。
    """

    id: str
    user_id: str
    creator_user_id: str
    appointment_at: datetime
    facility_id: Optional[str]
    hospital_name: str
    hospital_address: Optional[str]
    hospital_phone: Optional[str]
    department: str
    doctor_name: Optional[str]
    serial_number: Optional[str]
    note: Optional[str]
    status: AppointmentStatus
    departed_at: Optional[datetime]
    departed_by_user_id: Optional[str]
    attended_at: Optional[datetime]
    attended_by_user_id: Optional[str]
    enabled: bool
    notify_at: List[datetime]
    created_at: datetime
    updated_at: datetime


class CreateAppointmentReminderRequest(BaseModel):
    """POST /api/appointments/reminders。

    `appointment_at` 在這裡宣告成一般的 datetime，帶不帶時區都能通過解析；
    「必須帶 offset」由服務層檢查並回 400 與中文訊息，而不是讓 pydantic 回一個
    前端無法直接顯示的 422 清單。
    """

    user_id: str
    appointment_at: datetime
    facility_id: Optional[str] = None
    hospital_name: str
    hospital_address: Optional[str] = None
    hospital_phone: Optional[str] = None
    department: str
    doctor_name: Optional[str] = None
    serial_number: Optional[str] = None
    note: Optional[str] = None


class UpdateAppointmentReminderRequest(BaseModel):
    """PUT /api/appointments/reminders/{id}。

    這裡的 Optional 一律是「可以不帶」。服務層以 `model_dump(exclude_unset=True)`
    區分兩件事：**沒帶這個 key＝不動，帶了而且是 null＝清空。** 只有
    `NULLABLE_FIELDS` 的 null 有意義，其餘欄位送 null 一律 400。

    刻意不用 `exclude_none`：那會讓 null 在進服務層之前就被濾掉，使用者「填錯醫師
    名字想清空」永遠做不到——用藥提醒的 `end_date` 就是這樣卡住的。
    """

    appointment_at: Optional[datetime] = None
    facility_id: Optional[str] = None
    hospital_name: Optional[str] = None
    hospital_address: Optional[str] = None
    hospital_phone: Optional[str] = None
    department: Optional[str] = None
    doctor_name: Optional[str] = None
    serial_number: Optional[str] = None
    note: Optional[str] = None
    enabled: Optional[bool] = None

    NULLABLE_FIELDS: ClassVar[frozenset[str]] = NULLABLE_TEXT_FIELDS
