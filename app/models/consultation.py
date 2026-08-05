# 定義諮詢對話相關的 Pydantic 模型
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# 宣告常數並利用避免sonarQube跳警告
LINE_USER_ID_DESCRIPTION = "LINE user ID"


from app.models.chat_message import ChatMessage


class ConsultationSummary(BaseModel):
    line_id: str = Field(..., description=LINE_USER_ID_DESCRIPTION)
    summary_date: date = Field(..., description="摘要日期")
    summary: str = Field(..., description="摘要內容")
    language: Optional[str] = Field(default=None, description="摘要語言")
    created_at: datetime = Field(..., description="建立時間")


class ConsultationSummarizeRequest(BaseModel):
    target_date: Optional[date] = Field(default=None, description="要摘要的日期")
    force: bool = Field(default=False, description="是否強制重新摘要")


class ConsultationViewResponse(BaseModel):
    """諮詢紀錄查詢回傳"""

    line_id: str = Field(..., description=LINE_USER_ID_DESCRIPTION)
    view_type: Literal["summary", "raw"] = Field(..., description="回傳資料來源")
    summary: Optional[str] = Field(default=None, description="摘要內容")
    language: Optional[str] = Field(default=None, description="摘要語言")
    messages: list[ChatMessage] = Field(
        default_factory=list, description="原始對話訊息"
    )
