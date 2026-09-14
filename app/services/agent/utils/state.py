from typing import Optional
from langgraph.graph import MessagesState


class State(MessagesState):
    allow_rag: bool
    # 急迫度判斷的結果。emergency 時整個 agent 被短路，直接回緊急flex message。
    urgency: str
    urgency_display: str
    call_request_location: bool
    user_profile: Optional[dict]
