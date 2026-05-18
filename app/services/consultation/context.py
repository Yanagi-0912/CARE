# 處理 LINE 訊息的過程中存取諮詢相關資訊。
from __future__ import annotations

# contextManager 用來在特定程式區塊內設定和取得 ConsultationContext，
# 讓我們在處理 LINE 訊息的過程中能夠方便地存取當前的諮詢相關資訊，
# 例如使用者 ID、訊息類型、事件時間等。這樣的設計可以讓我們在不同的函式
# 或服務中共享這些資訊，而不需要每次都透過參數傳遞。
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional


# frozen=True 代表這個 dataclass 是不可變的
@dataclass(frozen=True)
class ConsultationContext:
    line_id: Optional[str]
    message_type: str
    event_time: Optional[datetime] = None
    raw_message: Optional[object] = None


_current_context: ContextVar[Optional[ConsultationContext]] = ContextVar(
    "consultation_context",
    default=None,
)


@contextmanager
def consultation_context_scope(
    context: ConsultationContext,
) -> Iterator[ConsultationContext]:
    token = _current_context.set(context)
    try:
        yield context
    finally:
        _current_context.reset(token)


def get_current_consultation_context() -> Optional[ConsultationContext]:
    return _current_context.get()
