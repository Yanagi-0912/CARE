import pytest

from app.services.medical.facility_type_matcher import (
    CANONICAL_FACILITY_TYPES,
    FACILITY_TYPE_ALIASES,
    FACILITY_TYPE_CATEGORIES,
    build_facility_type_query,
    extract_facility_type_intent,
    resolve_facility_type,
)


@pytest.mark.parametrize(
    ("text", "expected_category"),
    [
        # 三大類的裸詞本身就是 FACILITY_TYPE_CATEGORIES 的 key，必須直接命中
        ("醫院", "醫院"),
        ("診所", "診所"),
        ("藥局", "藥局"),
        # 資料庫實際 type 值（正式名稱）也應可直接解析出所屬分類
        ("綜合醫院", "醫院"),
        ("精神科醫院", "醫院"),
        ("中醫醫院", "醫院"),
        ("專科診所", "診所"),
        ("西醫診所", "診所"),
        ("藥師自營", "藥局"),
        ("藥劑生自營", "藥局"),
        # 別名
        ("大醫院", "醫院"),
        ("大型醫院", "醫院"),
        ("住院", "醫院"),
        ("小診所", "診所"),
        ("門診", "診所"),
        ("藥房", "藥局"),
        ("藥店", "藥局"),
        # 全形標點與空白
        ("醫院。", "醫院"),
        ("  診所  ", "診所"),
    ],
)
def test_resolve_facility_type(text, expected_category):
    match = resolve_facility_type(text)
    assert match is not None
    assert match.category == expected_category
    assert match.requested == text.strip().rstrip("。")


@pytest.mark.parametrize(
    "text",
    ["", "   ", "！！！", "隨便什麼字", "台大", "病理中心"],
)
def test_resolve_facility_type_returns_none_for_unknown(text):
    # 病理中心雖是資料庫 type 值之一，但民眾不會直接前往，不歸入任何使用者可選類型。
    assert resolve_facility_type(text) is None


@pytest.mark.parametrize("text", ["牙醫", "中醫"])
def test_resolve_facility_type_excludes_department_dimension(text):
    """
    「牙醫」「中醫」已存在於 department_matcher 的別名表（→ 牙科、中醫一般科）。
    若同時作為類型分類，同一個詞會映射到兩個維度並以 $and 疊加，反而縮小結果集
    （中醫一般科 3,517 筆 vs 中醫類型院所 3,496 筆，差額是綜合醫院的中醫部）。
    因此類型分類表必須排除這兩個詞，交給科別維度處理。
    """
    assert resolve_facility_type(text) is None


def test_resolve_facility_type_marks_alias():
    match = resolve_facility_type("大醫院")
    assert match.requested == "大醫院"
    assert match.category == "醫院"
    assert match.is_alias is True

    exact = resolve_facility_type("醫院")
    assert exact.is_alias is False


@pytest.mark.parametrize(
    ("sentence", "expected_category"),
    [
        ("附近有沒有醫院", "醫院"),
        ("我想找診所", "診所"),
        ("哪裡有藥局", "藥局"),
        ("我要住院治療", "醫院"),
        ("附近有大型醫院嗎", "醫院"),
        ("幫我找小診所", "診所"),
        ("附近有藥房嗎", "藥局"),
        ("今天天氣如何", None),
        ("附近有腸胃科嗎", None),  # 科別，不是類型
    ],
)
def test_extract_facility_type_intent(sentence, expected_category):
    match = extract_facility_type_intent(sentence)
    if expected_category is None:
        assert match is None
    else:
        assert match is not None
        assert match.category == expected_category


@pytest.mark.parametrize("sentence", ["我想看牙醫", "附近有中醫嗎"])
def test_extract_facility_type_intent_excludes_department_dimension(sentence):
    assert extract_facility_type_intent(sentence) is None


def test_extract_prefers_longest_candidate():
    """「大型醫院」不可被較短的「醫院」搶先命中，兩者解析結果剛好一致但語意應以長詞優先。"""
    match = extract_facility_type_intent("附近有大型醫院嗎")
    assert match is not None
    assert match.requested == "大型醫院"


def test_build_facility_type_query_uses_in_not_regex():
    """
    type 欄位乾淨且值互為子字串（「醫院」是「綜合醫院」「中醫醫院」「精神科醫院」的
    子字串），regex 無法區分，因此必須用 $in 精確比對。
    """
    query = build_facility_type_query("醫院")
    assert query == {
        "type": {"$in": ["醫院", "綜合醫院", "精神科醫院", "中醫醫院"]}
    }


def test_build_facility_type_query_clinic_category():
    query = build_facility_type_query("診所")
    assert query["type"]["$in"] == list(FACILITY_TYPE_CATEGORIES["診所"])


def test_build_facility_type_query_pharmacy_category():
    query = build_facility_type_query("藥局")
    assert query["type"]["$in"] == list(FACILITY_TYPE_CATEGORIES["藥局"])


def test_canonical_facility_types_has_all_17_values():
    assert len(CANONICAL_FACILITY_TYPES) == 17
    assert "病理中心" in CANONICAL_FACILITY_TYPES


def test_facility_type_categories_has_exactly_three_keys():
    assert set(FACILITY_TYPE_CATEGORIES.keys()) == {"醫院", "診所", "藥局"}


def test_all_category_values_exist_in_canonical_types():
    """守門測試：分類表裡的每個 type 值都必須是資料庫真的有的 17 個值之一。"""
    for values in FACILITY_TYPE_CATEGORIES.values():
        for value in values:
            assert value in CANONICAL_FACILITY_TYPES


def test_all_alias_targets_are_valid_categories():
    unknown = {
        target
        for target in FACILITY_TYPE_ALIASES.values()
        if target not in FACILITY_TYPE_CATEGORIES
    }
    assert unknown == set()
