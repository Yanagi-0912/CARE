# 定義諮詢對話相關的 Pydantic 模型
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# 宣告常數並利用避免sonarQube跳警告
LINE_USER_ID_DESCRIPTION = "LINE user ID"


class ConsultationMessage(BaseModel):

    line_id: str = Field(..., description=LINE_USER_ID_DESCRIPTION)
    message_type: str = Field(..., description="訊息類型，例如 text / location / image")
    content: str = Field(..., description="可供摘要與顯示的訊息內容")
    raw_text: Optional[str] = Field(default=None, description="原始文字或原始媒體描述")
    timestamp: datetime = Field(..., description="UTC timestamp")


class ConsultationSummary(BaseModel):
    line_id: str = Field(..., description=LINE_USER_ID_DESCRIPTION)
    summary_date: date = Field(..., description="摘要日期")
    summary: str = Field(..., description="摘要內容")
    created_at: datetime = Field(..., description="建立時間")


class ConsultationSummarizeRequest(BaseModel):
    target_date: Optional[date] = Field(default=None, description="要摘要的日期")
    force: bool = Field(default=False, description="是否強制重新摘要")


class ConsultationViewResponse(BaseModel):
    """諮詢紀錄查詢回傳"""

    line_id: str = Field(..., description=LINE_USER_ID_DESCRIPTION)
    view_type: Literal["summary", "raw"] = Field(..., description="回傳資料來源")
    summary: Optional[str] = Field(default=None, description="摘要內容")
    messages: list[ConsultationMessage] = Field(
        default_factory=list, description="原始對話訊息"
    )
