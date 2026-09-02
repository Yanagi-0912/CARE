"""
口語症狀 → 對照表條目的比對層。

規則比對層（手寫別名、異體字折疊、人稱／掛號意圖 regex 剝除）已整層移除，
理由見 normalizer 模組註解與 openspec/changes/symptom-department-guidance/
coverage.md。原本斷言「每一條別名都真的生效」「別名覆蓋率不得低於 75%」
「人稱前綴要全部剝掉」的測試隨該層一併刪除——那些性質已不存在。

留下來的是兩類仍然成立的斷言：
  1. 對照表本身的內容（本專案補列的條目、跨科不擇一），原本經由別名觸達，
     現在直接查表。
  2. 孩童指涉偵測，服務的是兒科候選過濾，不屬於比對層。
"""

import pytest

from app.services.medical.symptom_classification.normalizer import mentions_child
from app.services.medical.symptom_classification.symptom_table import (
    load_symptom_table,
)


@pytest.fixture(scope="module")
def table():
    return load_symptom_table()


# --- 對照表內容 --------------------------------------------------------------


def test_nosebleed_goes_to_ent(table):
    """
    三份來源都沒有鼻出血條目，是本專案補列的。實測「流鼻血要掛哪科」原本落到
    保底，看起來像耳鼻喉科被歸錯，其實是表沒收這個症狀。
    """
    entry = table.lookup("流鼻血")
    assert [c.canonical for c in entry.candidates] == ["耳鼻喉科"]


def test_phlegm_maps_to_both_airway_departments(table):
    """痰的來源可能在下呼吸道或上呼吸道，不該擇一。"""
    entry = table.lookup("痰多")
    assert {c.canonical for c in entry.candidates} == {"內科", "耳鼻喉科"}


def test_project_added_entry_claims_no_source_consensus(table):
    """
    `痰多` 是本專案補列而非來源所載，sources 為空。回覆不得宣稱有跨院共識，
    這靠 source_count 為 0 自然呈現。
    """
    for term in ("痰多", "流鼻血"):
        entry = table.lookup(term)
        assert all(c.source_count == 0 for c in entry.candidates)


# --- 孩童指涉偵測 ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["小孩發燒", "我兒子肚子痛", "女兒一直咳", "寶寶不吃東西", "孫子發燒", "幼兒腹瀉"],
)
def test_child_reference_is_detected(text):
    assert mentions_child(text) is True


@pytest.mark.parametrize(
    "text",
    ["我肚子好痛", "頭痛要掛哪一科", "我阿公中風了", "", "發燒"],
)
def test_adult_or_elder_text_is_not_a_child_reference(text):
    """阿公、阿嬤不是孩童指涉——把長輩誤判成孩童會給出兒科建議。"""
    assert mentions_child(text) is False


# --- 三段式比對 --------------------------------------------------------------
#
# 向量負責召回、LLM 負責決選（design 決策 12）。這裡驗的是分流本身，
# 不驗 embedding 的品質——那是 coverage.md 的量測範圍。

from app.services.medical.symptom_classification.normalizer import (  # noqa: E402
    SymptomNormalizer,
)
from app.services.medical.symptom_classification.vector_index import (  # noqa: E402
    build_index,
)

_FAKE_TERMS = ("腹痛", "咳嗽", "青光眼", "高血壓")
_FAKE_VECTORS = (
    [1.0, 0.0, 0.0, 0.0],
    [0.0, 1.0, 0.0, 0.0],
    [0.0, 0.0, 1.0, 0.0],
    [0.0, 0.0, 0.0, 1.0],
)


def _normalizer(query_vector, *, invoke=None, **kwargs):
    async def embed(_text):
        return query_vector

    return SymptomNormalizer(
        table_terms=_FAKE_TERMS,
        vector_index=build_index(_FAKE_TERMS, _FAKE_VECTORS),
        embed_query=embed,
        invoke=invoke,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_high_score_is_accepted_without_calling_the_llm():
    async def never(_prompt):
        raise AssertionError("高分不應呼叫 LLM 決選")

    normalizer = _normalizer([1.0, 0.0, 0.0, 0.0], invoke=never)
    assert await normalizer.resolve("肚子痛") == "腹痛"


@pytest.mark.asyncio
async def test_low_score_falls_back_without_calling_the_llm():
    """低於門檻一律走保底，SHALL NOT 退化成「取表中最接近的一條」。"""

    async def never(_prompt):
        raise AssertionError("低於門檻不應呼叫 LLM")

    # 與四個條目都接近正交 → 最高分遠低於 MIN_MATCH_SCORE
    normalizer = _normalizer([1.0, 1.0, 1.0, 1.0], invoke=never)
    assert await normalizer.resolve("今天天氣真好") is None


@pytest.mark.asyncio
async def test_mid_band_asks_the_llm_with_only_the_recalled_candidates():
    """
    中間帶的候選集合必須是向量召回的那幾個，不是全表——這正是「眼壓高」
    能從 top1「高血壓」救回「青光眼」的機制。
    """
    seen = {}

    async def invoke(prompt):
        seen["prompt"] = prompt
        return {"symptom": "青光眼"}

    # 偏向高血壓、但青光眼也在候選內
    normalizer = _normalizer([0.0, 0.0, 0.436, 0.900], invoke=invoke, top_k=2)
    assert await normalizer.resolve("眼壓高") == "青光眼"
    # 只驗「候選清單」那一行：prompt 的規則區塊寫死了「肚子痛→腹痛」當範例，
    # 對整段 prompt 做子字串比對會把那個範例誤判成候選。
    listed = seen["prompt"].split("候選清單：")[1].splitlines()[0]
    assert set(listed.split("、")) == {"高血壓", "青光眼"}


@pytest.mark.asyncio
async def test_llm_answer_outside_the_recalled_set_is_rejected():
    """enum 的強制力取決於模型與 SDK，放行集合外的值會讓後續查表靜默落空。"""

    async def invoke(_prompt):
        return {"symptom": "咳嗽"}

    normalizer = _normalizer([0.0, 0.0, 0.436, 0.900], invoke=invoke, top_k=2)
    assert await normalizer.resolve("眼壓高") is None


@pytest.mark.asyncio
async def test_embedding_failure_degrades_to_full_table_llm():
    """
    取向量失敗是常態（網路、配額）。此時退回全表 enum 交 LLM——降級，
    不是中斷；服務層不該因為向量拿不到就整條斷掉。
    """
    seen = {}

    async def embed(_text):
        raise RuntimeError("embedding API 掛了")

    async def invoke(prompt):
        seen["prompt"] = prompt
        return {"symptom": "腹痛"}

    normalizer = SymptomNormalizer(
        table_terms=_FAKE_TERMS,
        vector_index=build_index(_FAKE_TERMS, _FAKE_VECTORS),
        embed_query=embed,
        invoke=invoke,
    )
    assert await normalizer.resolve("肚子痛") == "腹痛"
    for term in _FAKE_TERMS:
        assert term in seen["prompt"], "降級後候選應為全表"


@pytest.mark.asyncio
async def test_no_index_still_works_via_llm():
    """向量檔不存在時（尚未 build），比對層仍須可用。"""

    async def invoke(_prompt):
        return {"symptom": "腹痛"}

    normalizer = SymptomNormalizer(table_terms=_FAKE_TERMS, invoke=invoke)
    assert await normalizer.resolve("肚子痛") == "腹痛"


@pytest.mark.asyncio
async def test_schema_never_gains_a_department_field_even_when_narrowed():
    """決策 5 的紅線在候選縮小後仍然成立：模型沒有輸出科別的通道。"""
    from app.services.medical.department_matcher import CANONICAL_DEPARTMENTS

    normalizer = _normalizer([1.0, 0.0, 0.0, 0.0])
    for candidates in (None, ("青光眼", "高血壓")):
        schema = normalizer._build_schema(candidates)
        assert set(schema["properties"]) == {"symptom"}
        assert not (set(schema["properties"]["symptom"]["enum"]) & CANONICAL_DEPARTMENTS)
