import logging

import pytest

from app.core.request_context import (
    clear_request_id,
    get_request_id,
    new_request_id,
    reset_request_id,
    set_request_id,
)
from app.core.request_logging import log_done, log_stage, log_start


def test_new_request_id_is_short_and_unique():
    a = new_request_id()
    b = new_request_id()
    assert len(a) == 8
    assert len(b) == 8
    assert a != b


def test_set_and_reset_request_id():
    clear_request_id()
    assert get_request_id() == "-"
    token = set_request_id("abc12def")
    assert get_request_id() == "abc12def"
    reset_request_id(token)
    assert get_request_id() == "-"


def test_log_start_done_stage_include_readable_markers(caplog):
    clear_request_id()
    set_request_id("deadbeef")
    logger = logging.getLogger("test.request_logging")
    with caplog.at_level(logging.INFO, logger="test.request_logging"):
        log_start(logger, event="text", user_id="U1234567890", text_len=12)
        log_stage(logger, "guardrail", allow_rag=True, ms=15)
        log_done(logger, status="ok", total_ms=1200)

    messages = [r.getMessage() for r in caplog.records]
    assert any("START event=text" in m for m in messages)
    assert any("stage=guardrail" in m and "allow_rag=True" in m for m in messages)
    assert any("DONE status=ok" in m and "total_ms=1200" in m for m in messages)


def test_request_id_filter_adds_rid_to_log_record():
    from app.core.logging_setup import RequestIdFilter

    clear_request_id()
    set_request_id("cafebabe")
    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    assert RequestIdFilter().filter(record) is True
    assert record.rid == "cafebabe"
