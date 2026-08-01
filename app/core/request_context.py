"""Request-scoped context for correlating one LINE event across logs."""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

_request_id: ContextVar[str] = ContextVar("care_request_id", default="-")


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
