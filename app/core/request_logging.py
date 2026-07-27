"""Helpers for START / DONE / stage logs on a single request timeline."""

from __future__ import annotations

import logging
from typing import Any


def _format_kv(**fields: Any) -> str:
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    return " ".join(parts)


def log_start(logger: logging.Logger, *, event: str, **fields: Any) -> None:
    extra = _format_kv(**fields)
    if extra:
        logger.info("START event=%s %s", event, extra)
    else:
        logger.info("START event=%s", event)


def log_done(logger: logging.Logger, *, status: str, total_ms: int, **fields: Any) -> None:
    extra = _format_kv(**fields)
    if extra:
        logger.info("DONE status=%s total_ms=%s %s", status, total_ms, extra)
    else:
        logger.info("DONE status=%s total_ms=%s", status, total_ms)


def log_stage(logger: logging.Logger, stage: str, **fields: Any) -> None:
    extra = _format_kv(**fields)
    if extra:
        logger.info("stage=%s %s", stage, extra)
    else:
        logger.info("stage=%s", stage)
