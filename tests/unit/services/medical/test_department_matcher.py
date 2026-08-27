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


# --------------------------------------------------------------------------
# FuncType 官方科別代碼補充（來源見 docs/department-alias-functype-review.md）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_canonical"),
    [
        ("心臟血管內科", "內科"),
        ("心臟血管外科", "外科"),
        ("消化外科", "外科"),
        ("直腸外科", "外科"),
        ("新生兒科", "兒科"),
        ("脊椎骨科", "骨科"),
        ("結核科", "內科"),
        ("胸腔暨重症加護", "內科"),
        ("老人醫學科", "家醫科"),
        ("高齡醫學科", "家醫科"),
        ("疼痛科", "麻醉科"),
    ],
)
def test_functype_official_names_resolve(text, expected_canonical):
    """FuncType 的正式科別名稱必須查得到，否則每次都要付一次 LLM 兜底。"""
    match = resolve_department(text)
    assert match is not None
    assert match.canonical == expected_canonical


def test_oral_maxillofacial_variant_is_not_silently_downgraded():
    """
    官方代碼寫「口腔顏面外科」、資料庫寫「口腔顎面外科」。

    未收錄時 extract_department_intent 的子字串掃描會退而命中「外科」，產出
    canonical=外科、requested=外科、is_alias=False 的結果——答案是錯的，而且因為
    is_alias 為 False，連「你說的 X 歸類於 Y」的告知都不會觸發。這是「系統很有
    自信地答錯」，比「系統看不懂」更難察覺。
    """
    match = resolve_department("口腔顏面外科")
    assert match.canonical == "口腔顎面外科"

    in_sentence = extract_department_intent("附近有沒有口腔顏面外科")
    assert in_sentence.canonical == "口腔顎面外科"
    assert in_sentence.requested == "口腔顏面外科"
    assert in_sentence.is_alias is True


@pytest.mark.parametrize("text", ["潛醫科", "居家照護"])
def test_ambiguous_functype_entries_stay_unresolved(text):
    """
    刻意不收：潛醫科（高壓氧可設於急診／職醫／整外，無單一正解）與居家照護
    （服務類型而非科別，導向門診會答非所問）。猜錯的成本大於收錄的效益。
    """
    assert resolve_department(text) is None


# --------------------------------------------------------------------------
# 家醫科／家庭醫學科：資料庫兩個獨立值
# --------------------------------------------------------------------------


def test_family_medicine_normalizes_to_single_canonical():
    """
    資料庫原本「家醫科」與「家庭醫學科」兩個值並存，且「家醫科」不是
    「家庭醫學科」的連續子字串，查其中一種寫法會靜默漏掉另一種。已由
    scripts/normalize_family_medicine_department.py 在資料源頭統一為「家醫科」，
    使用者的說法則由別名表收斂。
    """
    match = resolve_department("家庭醫學科")
    assert match.canonical == "家醫科"
    assert match.requested == "家庭醫學科"
    assert match.is_alias is True

    assert extract_department_intent("附近有家庭醫學科嗎").canonical == "家醫科"


def test_family_medicine_is_not_a_canonical_value():
    """
    「家庭醫學科」若留在 CANONICAL_DEPARTMENTS，resolve_department 的精確命中
    會搶在別名之前回傳它，查詢便會用「家庭醫學科」的 regex——遷移後資料庫已無
    此值，結果是永遠 0 筆。
    """
    assert "家庭醫學科" not in CANONICAL_DEPARTMENTS
    assert build_department_query("家醫科") == {
        "departments": {"$regex": "家醫科", "$options": "i"}
    }


# --------------------------------------------------------------------------
# 回歸：本次擴充不得放寬「不從症狀推科別」的紅線
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symptom", ["肚子痛", "胸悶", "頭暈", "一直咳嗽", "發燒", "胃痛", "腰痛"]
)
def test_symptoms_still_never_resolve_to_a_department(symptom):
    """
    症狀分診屬醫療判斷，猜錯的代價是把可能需要急診的人導去一般門診。
    本模組只收科別別名，這條線不因為新增了官方代碼來源就放寬。
    """
    assert resolve_department(symptom) is None
    assert extract_department_intent(symptom) is None
