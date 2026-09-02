from app.core.rag_sources import (
    SourceRef,
    get_request_rag_sources,
    reset_request_rag_sources,
    set_request_rag_sources,
)


def test_default_is_empty():
    assert get_request_rag_sources() == ()


def test_set_and_reset_round_trip():
    refs = (SourceRef(index=1, label="食藥署", url="https://www.fda.gov.tw/x"),)

    token = set_request_rag_sources(refs)
    try:
        assert get_request_rag_sources() == refs
    finally:
        reset_request_rag_sources(token)

    assert get_request_rag_sources() == ()


def test_set_coerces_to_tuple():
    """存進去的必須是不可變序列，避免呼叫端事後改到已設定的值。"""
    token = set_request_rag_sources(
        [SourceRef(index=1, label="台灣 e 院", url="https://sp1.hso.mohw.gov.tw/x")]
    )
    try:
        assert isinstance(get_request_rag_sources(), tuple)
    finally:
        reset_request_rag_sources(token)


def test_source_ref_is_frozen():
    ref = SourceRef(index=1, label="食藥署", url="https://www.fda.gov.tw/x")
    try:
        ref.index = 2  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("SourceRef 應為 frozen dataclass")
