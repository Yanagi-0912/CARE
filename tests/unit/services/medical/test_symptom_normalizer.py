"""
口語症狀 → 對照表條目的比對層。

這一層的失敗是無聲的：查不到就回保底（家醫科／內科），卡片會說「無法對應到
已知的症狀條目」。使用者看到的是「這個症狀沒收」，實際上可能只是別名沒寫、
或表裡用了另一種寫法。因此這裡的測試多半在斷言「別名真的生效」，而不只是
「別名有寫」。
"""

import pytest

from app.services.medical.symptom_classification.normalizer import (
    SYMPTOM_ALIASES,
    SymptomNormalizer,
    normalize_text,
)
from app.services.medical.symptom_classification.symptom_table import (
    load_symptom_table,
)


@pytest.fixture(scope="module")
def table():
    return load_symptom_table()


@pytest.fixture(scope="module")
def normalizer(table):
    return SymptomNormalizer(table_terms=table.terms)


# --- 異體字折疊 --------------------------------------------------------------
#
# 表內同一個概念用了兩種寫法（`筋骨痠痛` 用痠、`肩頸酸痛` 用酸），使用者打哪
# 一種是隨機的。沒有折疊時，打錯邊就落到保底。


@pytest.mark.parametrize(
    "written, expected",
    [
        ("筋骨痠痛", "筋骨痠痛"),
        ("筋骨酸痛", "筋骨痠痛"),
        ("肩頸酸痛", "肩頸酸痛"),
        ("肩頸痠痛", "肩頸酸痛"),
        ("便秘", "便秘"),
        ("便祕", "便秘"),
    ],
)
def test_variant_characters_fold_to_the_same_entry(normalizer, written, expected):
    assert normalizer.resolve_from_table(written) == expected


def test_folding_returns_the_table_spelling_not_the_folded_form(normalizer, table):
    """
    回傳值必須是表內的原始寫法——它是 SymptomTable 的 key，回折疊後的形式
    會讓後續查表落空，退化成「系統說查過了但沒有結果」。
    """
    resolved = normalizer.resolve_from_table("筋骨酸痛")
    assert resolved in table.terms
    assert table.lookup(resolved) is not None


# --- 使用者實際回報的落空案例 ------------------------------------------------


@pytest.mark.parametrize(
    "user_input, expected_term",
    [
        ("肌肉無力", "肢體無力"),
        ("我肌肉無力要看哪一科", "肢體無力"),
        ("手腳沒力", "肢體無力"),
        ("全身痠痛", "肩頸腰背酸痛"),
        ("全身酸痛看哪科", "肩頸腰背酸痛"),
        ("喉嚨有痰", "痰多"),
        ("痰很多", "痰多"),
        ("一直有痰", "痰多"),
        # 第二輪實測回報：表缺條目與缺別名各一
        ("流鼻血要掛哪科", "流鼻血"),
        ("鼻血", "流鼻血"),
        ("我阿公中風後遺症要看哪一科", "中風復健"),
    ],
)
def test_reported_misses_now_resolve(normalizer, user_input, expected_term):
    assert normalizer.resolve_from_table(user_input) == expected_term


def test_nosebleed_goes_to_ent(normalizer, table):
    """
    三份來源都沒有鼻出血條目，是本專案補列的。實測「流鼻血要掛哪科」原本落到
    保底，看起來像耳鼻喉科被歸錯，其實是表沒收這個症狀。
    """
    entry = table.lookup(normalizer.resolve_from_table("流鼻血要掛哪科"))
    assert [c.canonical for c in entry.candidates] == ["耳鼻喉科"]


def test_phlegm_maps_to_both_airway_departments(normalizer, table):
    """痰的來源可能在下呼吸道或上呼吸道，不該擇一。"""
    entry = table.lookup(normalizer.resolve_from_table("喉嚨有痰"))
    assert {c.canonical for c in entry.candidates} == {"內科", "耳鼻喉科"}


def test_project_added_entry_claims_no_source_consensus(normalizer, table):
    """
    `痰多` 是本專案補列而非來源所載，sources 為空。回覆不得宣稱有跨院共識，
    這靠 source_count 為 0 自然呈現。
    """
    for term in ("痰多", "流鼻血"):
        entry = table.lookup(term)
        assert all(c.source_count == 0 for c in entry.candidates)


# --- 別名表的健康度 ----------------------------------------------------------


def test_no_alias_points_at_a_missing_entry():
    """
    指向表中不存在的條目時，線上會查到 None 並落到保底——比少寫一條別名
    更難察覺。
    """
    table_terms = set(load_symptom_table().terms)
    dangling = {k: v for k, v in SYMPTOM_ALIASES.items() if v not in table_terms}
    assert dangling == {}


def test_every_alias_actually_takes_effect(normalizer):
    """
    兩種無聲的死法：被表內同名條目遮蔽（`便血` 本身就是條目），
    或被 _INTENT_PATTERNS 剝掉前綴後與別條相撞（`孩子沒胃口` → `沒胃口`）。
    兩者都不會拋錯，只會讓那條別名靜靜地不存在。
    """
    ineffective = {
        key: (value, normalizer.resolve_from_table(key))
        for key, value in SYMPTOM_ALIASES.items()
        if normalizer.resolve_from_table(key) != value
    }
    assert ineffective == {}


def test_alias_coverage_stays_above_threshold(normalizer, table):
    """
    覆蓋率是這個功能能不能用的判準。初版是 17%（389 條裡只有 67 條有別名），
    使用者連打三個常見詞全部落空。門檻刻意訂在略低於現況，容許表新增條目，
    但掉回原本的水準會失敗。
    """
    terms = set(table.terms)
    covered = {v for v in SYMPTOM_ALIASES.values()} & terms
    ratio = len(covered) / len(terms)
    assert ratio >= 0.75, f"別名覆蓋率 {ratio:.0%}，低於門檻"


# --- 掛號意圖剝除 ------------------------------------------------------------


@pytest.mark.parametrize(
    "user_input",
    [
        "我肚子痛要掛哪一科",
        "肚子痛看什麼科",
        "請問肚子痛要看哪一科？",
        "我阿公肚子痛",
    ],
)
def test_intent_wrapper_is_stripped(normalizer, user_input):
    assert normalizer.resolve_from_table(user_input) == "腹痛"


def test_normalize_text_is_idempotent():
    """折疊與剝除套用兩次不得產生不同結果，否則快取 key 會不穩定。"""
    for text in ("我筋骨痠痛要掛哪一科", "全身酸痛", "喉嚨有痰"):
        once = normalize_text(text)
        assert normalize_text(once) == once


@pytest.mark.parametrize(
    "user_input, expected",
    [
        ("我阿公肚子痛", "腹痛"),
        ("我家小孩發燒", "發燒"),
        ("我家的小孩一直咳", "咳嗽"),
        ("我媽媽頭暈要看哪一科", "頭暈"),
        ("我阿嬤肌肉無力", "肢體無力"),
    ],
)
def test_stacked_person_prefixes_are_all_stripped(normalizer, user_input, expected):
    """
    「幫家人問」是這個功能最常見的用法之一，而人稱前綴會疊很多層。
    只剝一層時「我阿公肚子痛」會剩下「阿公肚子痛」；`我` 排在 `我家` 前面時
    「我家小孩發燒」會剩下「家小孩發燒」。兩種都靜靜地落到保底。
    """
    assert normalizer.resolve_from_table(user_input) == expected


# --- 孩童指涉偵測 ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["小孩發燒", "我兒子肚子痛", "女兒一直咳", "寶寶不吃東西", "孫子發燒", "幼兒腹瀉"],
)
def test_child_reference_is_detected(text):
    from app.services.medical.symptom_classification.normalizer import mentions_child

    assert mentions_child(text) is True


@pytest.mark.parametrize(
    "text",
    ["我肚子好痛", "頭痛要掛哪一科", "我阿公中風了", "", "發燒"],
)
def test_adult_or_elder_text_is_not_a_child_reference(text):
    """阿公、阿嬤不是孩童指涉——把長輩誤判成孩童會給出兒科建議。"""
    from app.services.medical.symptom_classification.normalizer import mentions_child

    assert mentions_child(text) is False
