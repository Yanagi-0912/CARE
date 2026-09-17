"""走失求救即時位置分享的 API 模型（見 app/routers/users/lost.py）。"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

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


class LostSelfStatus(BaseModel):
    """長輩定位頁看的狀態：還要不要繼續上傳。"""

    active: bool
    status: Optional[LostStatus] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


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
    server_time: datetime


class LostEndResult(BaseModel):
    ended: bool
    status: Optional[LostStatus] = None
