"""Central logging configuration for CARE."""

from __future__ import annotations

import logging
import os

from app.core.request_context import get_request_id

_CONFIGURED = False


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.rid = get_request_id()
        return True


def configure_logging(level: str | int | None = None) -> None:
    """Idempotent root logging setup with request id in every line."""
    global _CONFIGURED

    if level is None:
        level_name = os.getenv("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [rid=%(rid)s] %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    request_filter = RequestIdFilter()

    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        handler.addFilter(request_filter)
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            handler.setFormatter(formatter)
            handler.addFilter(request_filter)

    # Quiet noisy libraries so request timeline stays readable.
    for name in ("httpx", "httpcore", "urllib3", "asyncio", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)

    _CONFIGURED = True
