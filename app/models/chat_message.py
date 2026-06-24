from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

class ChatMessage(BaseModel):
    line_id: str = Field(..., description="使用者 ID / 會話 ID")
    message_type: str = Field(..., description="訊息類型，如 text, image, file, assistant_reply 等")
    content: str = Field(..., description="訊息的純文字內容")
    timestamp: datetime = Field(..., description="UTC 時間戳")
