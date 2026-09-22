from typing import Any, Optional
from langgraph.graph import MessagesState


class State(MessagesState):
    allow_rag: bool
    # guardrail 節點在等急迫度／guardrail 的同時先起跑的「選工具」LLM 呼叫
    # （asyncio.Task），或不必問模型時已經定好的決定（AIMessage）。agent 節點
    # 取用一次就清成 None——工具回來後那一步是在組回覆，不能再拿它。
    # 見 nodes.AgentNodes._start_speculative_decision。
    speculative_decision: Optional[Any]
    predecided: Optional[Any]
    # 急迫度判斷的結果。emergency 時整個 agent 被短路，直接回緊急flex message。
    urgency: str
    urgency_display: str
    call_request_location: bool
    user_profile: Optional[dict]
