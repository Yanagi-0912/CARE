from pydantic import BaseModel, Field
from typing import Any, Optional, Dict


# pydantic 是來做資料驗證的還有資料管理的，比一般的python class 好一點的是為自動檢查是否符合規則
# EX json 傳回來的是字串，像是有一欄是age就要把json的字串轉成int
# 有了pydantic 會先建立一個basemodel 的基本model
class AIRequest(BaseModel):
    """AI 回應請求模型"""

    user_input: str = Field(
        ...,  # 這個代表必填欄位，如果沒有傳就會報422錯誤
        description="使用者輸入的問題或訊息",  # api文件
        json_schema_extra={"example": "請告詞我台北市有哪些醫院？"},
    )


class AIResponse(BaseModel):
    """AI 回應成功模型"""

    response: str = Field(
        ...,
        description="AI 生成的回應內容",
        json_schema_extra={
            "example": "台北市有許多醫院，包括台大醫院、榮民總醫院等..."
        },
    )


class ErrorResponse(BaseModel):
    """錯誤回應模型"""

    error: str = Field(
        ...,
        description="錯誤訊息",
        json_schema_extra={"example": "user_input is required"},
    )


class HealthResponse(BaseModel):
    """健康檢查回應模型"""

    status: str = Field(
        ...,
        description="服務狀態訊息",
        json_schema_extra={"example": "Welcome to CARE Backend!"},
    )


class RootResponse(BaseModel):
    """根路徑回應模型"""

    message: str = Field(
        ...,
        description="歡迎訊息",
        json_schema_extra={"example": "CARE Backend Running"},
    )


class ClinicTimeSlot(BaseModel):
    """
    單一時段的營業/門診時間。例如:
    slots:[
        {
            "open": "08:00",
            "close": "12:00"
        },
        {
            "open": "13:30",
            "close": "17:30"
        }
    ]
    對應資料庫 clinicTime.<day>.slots 陣列中的元素。
    """

    open: str = Field(..., description="開始時間，例如：08:00")
    close: str = Field(..., description="結束時間，例如：17:30")


class ClinicDaySchedule(BaseModel):
    """
    單一天的營業/門診時間表。
    對應資料庫 clinicTime.<day> 欄位，例如 clinicTime.monday。
    """

    isClosed: bool = Field(..., description="當天是否公休")
    slots: list[ClinicTimeSlot] = Field(
        default_factory=list, description="當天的營業時段清單，公休時為空陣列"
    )


class MedicalFacility(BaseModel):
    """
    醫療院所資料模型。

    與未來 PostgreSQL 資料表欄位對齊，確保資料庫串接時格式一致。
    此模型同時作為 Gemini Function Calling 的回傳結構定義。
    """

    id: Optional[str] = Field(
        None, description="院所唯一識別碼（對應資料庫 primary key）"
    )
    name: str = Field(..., description="院所名稱，例如：台大醫院")
    latitude: float = Field(..., description="院所緯度座標")
    longitude: float = Field(..., description="院所經度座標")
    address: str = Field(..., description="院所完整地址")
    phone: Optional[str] = Field(None, description="院所聯絡電話")
    type: str = Field(..., description="院所類型，例如：醫院、診所、藥局")
    clinic_time: Optional[Dict[str, ClinicDaySchedule]] = Field(
        None,
        description=(
            "院所營業/門診時間，對應資料庫 clinicTime 欄位。"
            "key 為星期英文小寫（monday ~ sunday），"
            "value 為當天的公休狀態與時段清單。"
        ),
    )
    departments: Optional[list[str]] = Field(
        None, description="院所診療科別，對應資料庫 departments 欄位"
    )
    notes: Optional[str] = Field(
        None,
        description=(
            "院所補充註記，對應資料庫 notes 欄位。內容為自由文字且格式不規則，"
            "常見為節慶特殊開診資訊（如「春節假期2／17~2／22休診」）或"
            "長期性提醒（如「如需看診請先電話洽詢」）。"
        ),
    )
    distance_meters: Optional[float] = Field(
        None, description="距離用戶的直線距離（公尺），由 PostGIS 計算填入"
    )