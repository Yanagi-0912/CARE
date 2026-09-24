from typing import Any, Optional
from langgraph.graph import MessagesState

from app.services.medical.symptom_classification.urgency import UrgencyVerdict


class State(MessagesState):
    allow_rag: bool
    # guardrail 節點在等急迫度／guardrail 的同時先起跑的「選工具」LLM 呼叫
    # （asyncio.Task），或不必問模型時已經定好的決定（AIMessage）。agent 節點
    # 取用一次就清成 None——工具回來後那一步是在組回覆，不能再拿它。
    # 見 nodes.AgentNodes._start_speculative_decision。
    speculative_decision: Optional[Any]
    predecided: Optional[Any]
    # 急迫度判斷的結果。emergency 時整個 agent 被短路，直接回緊急flex message。
    # 整個判定原樣帶著走，不拆成字串：受影響人物（affected）要一路傳到紅卡送出
    # 之後的人物解析與通知，拆開再組回來只會掉欄位。
    urgency: UrgencyVerdict
    call_request_location: bool
    user_profile: Optional[dict]
