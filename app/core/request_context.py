"""Request-scoped context for correlating one LINE event across logs."""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

_request_id: ContextVar[str] = ContextVar("care_request_id", default="-")
_line_user_id: ContextVar[str | None] = ContextVar("line_user_id", default=None)

def new_request_id() -> str:
    return uuid.uuid4().hex[:8]


def get_request_id() -> str:
    return _request_id.get()


def set_request_id(request_id: str) -> Token:
    return _request_id.set(request_id)


def reset_request_id(token: Token) -> None:
    _request_id.reset(token)


def clear_request_id() -> None:
    _request_id.set("-")

def set_line_user_id(user_id: str) -> Token:
    return _line_user_id.set(user_id)

def reset_line_user_id(token: Token) -> None:
    _line_user_id.reset(token)
    
def get_line_user_id() -> str | None:
    return _line_user_id.get()