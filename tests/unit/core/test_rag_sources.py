from app.core.rag_sources import (
    SourceRef,
    begin_request_rag_sources,
    get_request_rag_sources,
    reset_request_rag_sources,
    set_request_rag_sources,
)


def test_default_is_empty():
    assert get_request_rag_sources() == ()


def test_set_and_reset_round_trip():
    refs = (SourceRef(index=1, label="食藥署", url="https://www.fda.gov.tw/x"),)

    token = begin_request_rag_sources()
    try:
        set_request_rag_sources(refs)
        assert get_request_rag_sources() == refs
    finally:
        reset_request_rag_sources(token)

    assert get_request_rag_sources() == ()


def test_get_returns_immutable_snapshot():
    """讀出來的必須是不可變序列，呼叫端事後改它不得影響本輪來源。"""
    token = begin_request_rag_sources()
    try:
        set_request_rag_sources(
            [SourceRef(index=1, label="台灣 e 院", url="https://sp1.hso.mohw.gov.tw/x")]
        )
        assert isinstance(get_request_rag_sources(), tuple)
    finally:
        reset_request_rag_sources(token)


def test_set_without_holder_is_ignored():
    """沒有開場的入口（純 API、eval 腳本）不得因為設定來源而爆掉。"""
    set_request_rag_sources(
        [SourceRef(index=1, label="食藥署", url="https://www.fda.gov.tw/x")]
    )

    assert get_request_rag_sources() == ()


def test_each_turn_gets_a_fresh_holder():
    """開場換新 list，上一輪的來源不會被下一輪就地改寫波及。"""
    outer = begin_request_rag_sources()
    try:
        set_request_rag_sources(
            [SourceRef(index=1, label="上一輪", url="https://example.com/a")]
        )
        snapshot = get_request_rag_sources()

        inner = begin_request_rag_sources()
        try:
            set_request_rag_sources(
                [SourceRef(index=1, label="這一輪", url="https://example.com/b")]
            )
        finally:
            reset_request_rag_sources(inner)

        assert get_request_rag_sources() == snapshot
    finally:
        reset_request_rag_sources(outer)


def test_source_ref_is_frozen():
    ref = SourceRef(index=1, label="食藥署", url="https://www.fda.gov.tw/x")
    try:
        ref.index = 2  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("SourceRef 應為 frozen dataclass")
