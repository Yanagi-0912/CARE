"""Helpers for START / DONE / stage logs on a single request timeline."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
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


@contextmanager
def stage_timer(
    logger: logging.Logger, stage: str, **fields: Any
) -> Iterator[dict[str, Any]]:
    """計時一段區塊，離開時輸出 `stage=<stage> ms=<耗時>`。

    yield 出來的 dict 供呼叫端補上「跑完才知道」的欄位（命中數、是否降級
    等）；與 *fields* 同鍵時以它為準。

    耗時在 `finally` 輸出，**例外路徑也會記錄**——這條管線裡最值得量的正是
    逾時與失敗那幾條路（Cohere 逾時、Firecrawl scrape 失敗），成功才記錄
    的計時器剛好漏掉它們。計時器本身不吞例外，只負責記錄後原樣往上拋。
    """
    t0 = time.perf_counter()
    extra: dict[str, Any] = {}
    try:
        yield extra
    finally:
        log_stage(
            logger,
            stage,
            ms=int((time.perf_counter() - t0) * 1000),
            **{**fields, **extra},
        )
