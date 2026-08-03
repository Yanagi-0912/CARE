import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# 24 小時制 HH:MM
HHMM_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

MedicationSlotType = Literal["morning", "noon", "evening", "bedtime"]
MedicationLogStatus = Literal["pending", "taken", "missed"]

DEFAULT_SLOT_TIMES: dict[str, str] = {
    "morning": "08:00",
    "noon": "12:00",
    "evening": "18:00",
    "bedtime": "21:30",
}

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
    scheduled_time: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    enabled: Optional[bool] = None

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
