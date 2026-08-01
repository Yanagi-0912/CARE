from datetime import datetime, timezone
from typing import List, Literal, Optional
from pydantic import BaseModel, Field

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
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class UpdateMedicationReminderRequest(BaseModel):
    scheduled_time: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    enabled: Optional[bool] = None


class MedicationReminderResponse(BaseModel):
    reminder: MedicationReminder


class MedicationLogResponse(BaseModel):
    log: MedicationLog
