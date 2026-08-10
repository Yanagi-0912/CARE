import pytest

from app.services.medical.department_matcher import (
    CANONICAL_DEPARTMENTS,
    DEPARTMENT_ALIASES,
    build_department_query,
    extract_department_intent,
    resolve_department,
)


@pytest.mark.parametrize(
    ("text", "expected_canonical"),
    [
        # 資料庫沒有次專科，內科系一律歸「內科」
        ("腸胃科", "內科"),
        ("胃腸科", "內科"),
        ("肝膽腸胃科", "內科"),
        ("心臟科", "內科"),
        ("腎臟科", "內科"),
        ("新陳代謝科", "內科"),
        # 正式名稱直接命中
        ("內科", "內科"),
        ("耳鼻喉科", "耳鼻喉科"),
        ("骨科", "骨科"),
        # 別名
        ("身心科", "精神科"),
        ("神經內科", "神經科"),
        ("腦神經外科", "神經外科"),
        ("小兒科", "兒科"),
        ("婦科", "婦產科"),
        ("中醫", "中醫一般科"),
        ("針灸", "中醫一般科"),
        ("牙醫", "牙科"),
        ("洗腎", "洗腎科"),
        # 大腸直腸系：使用者實際會講「大腸科」，但那不是正式掛牌名稱
        ("大腸科", "外科"),
        ("大腸直腸科", "外科"),
        ("直腸科", "外科"),
        ("肛門科", "外科"),
        ("痔瘡", "外科"),
        # 缺「科」字時自動補上再比對
        ("腸胃", "內科"),
        ("復健", "復健科"),
        # 全形標點與台/臺差異
        ("腸胃科。", "內科"),
        ("台大", None),  # 院所名不是科別，必須解析失敗
    ],
)
def test_resolve_department(text, expected_canonical):
    match = resolve_department(text)
    if expected_canonical is None:
        assert match is None
    else:
        assert match is not None
        assert match.canonical == expected_canonical


def test_resolve_department_marks_alias():
    """別名要保留使用者原始說法，回覆時才能說明「腸胃科屬於內科」。"""
    match = resolve_department("腸胃科")
    assert match.requested == "腸胃科"
    assert match.canonical == "內科"
    assert match.is_alias is True

    exact = resolve_department("內科")
    assert exact.is_alias is False


@pytest.mark.parametrize("text", ["", "   ", "！！！", "隨便什麼字"])
def test_resolve_department_returns_none_for_unknown(text):
    assert resolve_department(text) is None


@pytest.mark.parametrize(
    ("sentence", "expected_canonical"),
    [
        ("附近有腸胃科嗎", "內科"),
        ("我想找腸胃科", "內科"),
        ("幫我找附近的牙科診所", "牙科"),
        ("哪裡有中醫", "中醫一般科"),
        ("最近的耳鼻喉科在哪", "耳鼻喉科"),
        ("我要看小兒科", "兒科"),
        ("附近有大腸科嗎", "外科"),
        ("附近有醫院嗎", None),  # 沒指定科別
        ("今天天氣如何", None),
    ],
)
def test_extract_department_intent(sentence, expected_canonical):
    match = extract_department_intent(sentence)
    if expected_canonical is None:
        assert match is None
    else:
        assert match is not None
        assert match.canonical == expected_canonical


def test_extract_prefers_longest_candidate():
    """「腸胃科」不可被較短的別名（如「胃」開頭的詞）搶先命中。"""
    match = extract_department_intent("我最近腸胃科想掛號，附近有嗎")
    assert match.canonical == "內科"


def test_build_department_query_uses_regex_for_dirty_data():
    """
    約 12 筆院所的 departments 是「['家醫科、內科、外科、…']」這種整串塞一格的
    髒資料（多為醫學中心）。精確比對會漏掉它們，因此必須用 regex。
    """
    query = build_department_query("內科")
    assert query == {"departments": {"$regex": "內科", "$options": "i"}}

    dirty_value = "家醫科、內科、外科、兒科、婦產科、骨科、神經外科"
    import re

    pattern = query["departments"]["$regex"]
    assert re.search(pattern, dirty_value)


def test_all_alias_targets_exist_in_database():
    """別名的對應目標必須是資料庫真的有的部定專科，否則查詢永遠 0 筆。"""
    unknown = {
        target
        for target in DEPARTMENT_ALIASES.values()
        if target not in CANONICAL_DEPARTMENTS
    }
    assert unknown == set()
