"""走失求救即時位置分享的 API 模型（見 app/routers/users/lost.py）。"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

LostStatus = Literal["active", "found", "safe", "expired"]


class LostLocationUpload(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    # 瀏覽器 Geolocation 的 accuracy（公尺，95% 信賴半徑）。上限只擋明顯錯誤的值。
    accuracy: Optional[float] = Field(default=None, ge=0, le=100_000)


class LostLocationPoint(BaseModel):
    latitude: float
    longitude: float
    accuracy: Optional[float] = None
    source: Optional[str] = None
    received_at: datetime


class LostTrailPoint(BaseModel):
    latitude: float
    longitude: float
    at: datetime


class LostFamilyMember(BaseModel):
    """長輩定位頁上的一位家人。位置只在他按了「我去找他」之後才有。"""

    name: str
    online: bool
    coming: bool
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_at: Optional[datetime] = None
    distance_m: Optional[float] = None


class LostSelfStatus(BaseModel):
    """長輩定位頁看的狀態：還要不要繼續上傳、哪些家人正在看或正在過來。"""

    active: bool
    status: Optional[LostStatus] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    family: List[LostFamilyMember] = Field(default_factory=list)


class LostPresenceUpload(BaseModel):
    """家人地圖頁每次輪詢時的回報。`coming` 為 True 才帶位置。"""

    coming: bool = False
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    accuracy: Optional[float] = Field(default=None, ge=0, le=100_000)

    @model_validator(mode="after")
    def _location_pair(self) -> "LostPresenceUpload":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude 與 longitude 要一起給")
        return self


class LostPresenceResult(BaseModel):
    recorded: bool


class LostSessionView(BaseModel):
    """家人地圖頁看的內容。"""

    session_id: str
    status: LostStatus
    intent: Literal["lost", "share"]
    patient_name: str
    patient_words: str
    started_at: datetime
    auto_end_at: datetime
    ended_at: Optional[datetime] = None
    ended_by_name: Optional[str] = None
    last_location: Optional[LostLocationPoint] = None
    last_seen_at: Optional[datetime] = None
    stale: bool
    stale_after_seconds: int
    trail: List[LostTrailPoint]
    # 看地圖的這位家人自己按了「我去找他」沒有。重新整理頁面後按鈕狀態要接得上，
    # 不然頁面一打開就回報「沒要過去」，會把他的位置從長輩畫面上清掉。
    viewer_coming: bool = False
    server_time: datetime


class LostEndResult(BaseModel):
    ended: bool
    status: Optional[LostStatus] = None
