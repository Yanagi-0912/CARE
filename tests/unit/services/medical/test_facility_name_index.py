"""
院所名稱索引：用「這串字是不是登記在案的院所名稱」取代字元形態猜測。

索引透過 configure_facility_names() 注入（正式環境由 app/dependencies.py
在啟動時載入全部 19,528 筆），因此測試不需要連資料庫，也不需要 monkey patch。
"""

import pytest

from app.services.medical.facility_name_index import (
    MAX_FACILITY_NAME_LENGTH,
    configure_facility_names,
    covers_known_facility_name,
    is_index_loaded,
    is_known_facility_name,
)

# 取自真實資料庫。刻意選這些名稱：它們以高頻用字（家、的、有）結尾或含括號，
# 正是先前三輪字元規則反覆誤判的那一批。
REAL_NAMES = frozenset(
    {
        "皇家診所",
        "全家診所",
        "美的診所",
        "一家牙醫診所",
        "金小兒科診所(光榮聯合診所)",
        "臺中市立老人復健綜合醫院(委託財團法人中國醫藥大學興建經營)",
    }
)


@pytest.fixture(autouse=True)
def loaded_index():
    configure_facility_names(REAL_NAMES)
    yield
    configure_facility_names(frozenset())


def test_index_reports_loaded_state():
    assert is_index_loaded() is True
    configure_facility_names(frozenset())
    assert is_index_loaded() is False


def test_known_name_lookup():
    assert is_known_facility_name("皇家診所") is True
    assert is_known_facility_name("好的診所") is False
    assert is_known_facility_name("") is False


def test_names_are_normalized_on_ingest():
    """
    索引寫入時就要正規化，否則與查詢端永遠對不上。

    「金小兒科診所(光榮聯合診所)」在查詢端會被去掉括號，
    若索引存原始字串就查不到 —— 這是實際發生過的 bug，
    全量掃描時造成 16 筆誤判。
    """
    assert is_known_facility_name("金小兒科診所光榮聯合診所") is True


def test_covers_matches_type_word_at_end_of_name():
    text = "皇家診所在哪"
    # 「診所」位於 index 2..4，落在名稱「皇家診所」範圍內
    assert covers_known_facility_name(text, 2, 4) is True


def test_covers_matches_type_word_in_middle_of_name():
    """
    類型詞未必在名稱尾巴 —— 這是「包住」而非「前綴以名稱結尾」的理由。
    「金小兒科診所光榮聯合診所」的第一個「診所」在中間。
    """
    text = "金小兒科診所光榮聯合診所"
    assert covers_known_facility_name(text, 4, 6) is True


def test_covers_rejects_generic_phrase():
    for text, start, end in [
        ("好的診所", 2, 4),
        ("評價不錯的診所", 5, 7),
        ("多家診所", 2, 4),
    ]:
        assert covers_known_facility_name(text, start, end) is False, text


def test_covers_returns_false_when_index_not_loaded():
    """索引未載入時一律回 False：漏判而非誤判，退回未套過濾的現況行為。"""
    configure_facility_names(frozenset())
    assert covers_known_facility_name("皇家診所在哪", 2, 4) is False


@pytest.mark.parametrize(
    ("start", "end"),
    [(-1, 4), (2, 2), (5, 3)],
)
def test_covers_rejects_invalid_span(start, end):
    assert covers_known_facility_name("皇家診所在哪", start, end) is False


def test_search_window_is_bounded_by_max_name_length():
    """
    比對成本必須與輸入長度無關。給一段遠長於名稱上限的文字，
    且把已知名稱放在超出視窗的位置，應查不到。
    """
    filler = "台" * (MAX_FACILITY_NAME_LENGTH + 10)
    text = "皇家診所" + filler
    # 類型詞位置在最尾端，已知名稱在開頭、距離超過視窗
    start = len(text) - 2
    assert covers_known_facility_name(text, start, start + 2) is False
