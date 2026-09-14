"""
症狀向量索引。

這一層的失敗多半是無聲的：向量與對照表不同步時仍算得出分數、仍查得到條目，
只是對到錯的東西。因此測試的重點不在「搜尋會不會動」，而在「不該用的時候
有沒有拒絕使用」。
"""

import json

import pytest

from app.services.medical.symptom_classification.vector_index import (
    SymptomVectorIndex,
    build_index,
    table_content_hash,
)

TERMS = ("腹痛", "咳嗽", "頭暈")
VECTORS = ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0])
MODEL = "test-embedding-model"


@pytest.fixture
def index():
    return build_index(TERMS, VECTORS, embedding_model=MODEL)


def _rewrite(path, **changes):
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(changes)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_search_ranks_by_cosine(index):
    matches = index.search([0.9, 0.3, 0.0], k=3)
    assert [m.term for m in matches] == ["腹痛", "咳嗽", "頭暈"]
    assert matches[0].score > matches[1].score > matches[2].score


def test_search_normalizes_so_magnitude_does_not_matter(index):
    """分數必須是夾角，不是長度——否則門檻會隨輸入長短漂移。"""
    small = index.search([0.1, 0.0, 0.0], k=1)[0].score
    large = index.search([100.0, 0.0, 0.0], k=1)[0].score
    assert small == pytest.approx(large)
    assert small == pytest.approx(1.0)


def test_search_rejects_wrong_dimension(index):
    """換了模型或 output_dimensionality 卻沒重算索引，必須炸開而不是給錯答案。"""
    with pytest.raises(ValueError, match="維度"):
        index.search([1.0, 0.0], k=1)


def test_build_index_takes_dimension_from_data(index):
    """維度是實際事實，不是常數——常數改了但向量沒重算時要在搜尋時被抓到。"""
    assert index.search([1.0, 0.0, 0.0], k=1)[0].term == "腹痛"


# --- 持久化：不該用的時候要拒絕使用 ------------------------------------------


def test_round_trip_preserves_search(index, tmp_path):
    path = tmp_path / "v.json"
    index.save(path)
    loaded = SymptomVectorIndex.load(path, expected_hash=index.table_hash, expected_model=MODEL)
    assert loaded is not None
    assert loaded.terms == index.terms
    assert loaded.search([0.9, 0.3, 0.0], k=1)[0].term == "腹痛"


def test_load_refuses_when_table_changed(index, tmp_path):
    """
    人工審定會逐條改寫 term。改完之後舊向量仍算得出分數、仍查得到條目，
    只是對到錯的東西——這是本模組最危險的失敗，必須擋在載入。
    """
    path = tmp_path / "v.json"
    index.save(path)
    changed = table_content_hash(("腹痛", "咳嗽", "眩暈"))
    assert SymptomVectorIndex.load(path, expected_hash=changed, expected_model=MODEL) is None


def test_load_returns_none_when_missing(tmp_path):
    """檔案不存在是可回復的降級（退回 LLM 兜底），不該讓服務起不來。"""
    assert SymptomVectorIndex.load(tmp_path / "nope.json", expected_hash="x", expected_model=MODEL) is None


def test_load_refuses_unknown_format_version(index, tmp_path):
    path = tmp_path / "v.json"
    index.save(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["format_version"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert SymptomVectorIndex.load(path, expected_hash=index.table_hash, expected_model=MODEL) is None


def test_load_returns_none_on_corrupt_file(tmp_path):
    path = tmp_path / "v.json"
    path.write_text("{ not json", encoding="utf-8")
    assert SymptomVectorIndex.load(path, expected_hash="x", expected_model=MODEL) is None


def test_hash_changes_when_any_term_changes():
    assert table_content_hash(("a", "b")) != table_content_hash(("a", "c"))
    assert table_content_hash(("a", "b")) != table_content_hash(("a", "b", "c"))


def test_hash_is_stable_for_equal_content():
    """同一份內容（不同的物件）要算出同一個 hash，否則每次重啟都會拒用向量檔。"""
    first = table_content_hash(("a", "b"))
    second = table_content_hash(tuple(["a", "b"]))
    assert first == second


def test_terms_and_vectors_must_match():
    with pytest.raises(ValueError, match="不符"):
        SymptomVectorIndex(terms=("a", "b"), vectors=([1.0],), table_hash="x", dim=1)


def test_every_vector_must_have_the_index_dimension():
    """search() 逐維相乘用的是 zip，長度不符的向量會被靜默截斷、給出錯的分數。"""
    with pytest.raises(ValueError, match="維度"):
        SymptomVectorIndex(
            terms=("a", "b"), vectors=([1.0, 0.0], [1.0]), table_hash="x", dim=2
        )


@pytest.mark.parametrize("root", ["[]", '"text"', "null", "3"])
def test_load_returns_none_when_the_root_is_not_an_object(tmp_path, root):
    path = tmp_path / "v.json"
    path.write_text(root, encoding="utf-8")
    assert SymptomVectorIndex.load(path, expected_hash="x", expected_model=MODEL) is None


def test_load_refuses_a_different_task_type(index, tmp_path):
    """RETRIEVAL_DOCUMENT 的向量與 SEMANTIC_SIMILARITY 的查詢同維度，卻不可比。"""
    path = tmp_path / "v.json"
    index.save(path)
    _rewrite(path, model_task_type="RETRIEVAL_DOCUMENT")
    assert SymptomVectorIndex.load(path, expected_hash=index.table_hash, expected_model=MODEL) is None


def test_load_refuses_a_different_embedding_model(index, tmp_path):
    """換成同維度的另一個模型時，其餘每一道檢查都會通過。"""
    path = tmp_path / "v.json"
    index.save(path)
    assert (
        SymptomVectorIndex.load(
            path, expected_hash=index.table_hash, expected_model="another-model"
        )
        is None
    )


def test_load_refuses_a_version_1_file_that_does_not_record_the_model(index, tmp_path):
    path = tmp_path / "v.json"
    index.save(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("embedding_model")
    payload["format_version"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert SymptomVectorIndex.load(path, expected_hash=index.table_hash, expected_model=MODEL) is None


# --- 落地的向量檔 ------------------------------------------------------------


def test_shipped_vectors_match_the_shipped_table():
    """
    repo 裡的向量檔必須與 repo 裡的對照表同步。不同步時線上會靜默降級成
    LLM 全表兜底，命中率掉回沒有向量的水準，而且只有日誌會說。
    """
    from app.core.config import settings
    from app.services.medical.symptom_classification.symptom_table import (
        load_symptom_table,
    )
    from app.services.medical.symptom_classification.vector_index import (
        DEFAULT_VECTOR_PATH,
    )

    table = load_symptom_table()
    loaded = SymptomVectorIndex.load(
        DEFAULT_VECTOR_PATH,
        expected_hash=table_content_hash(table.terms),
        expected_model=settings.EMBEDDING_MODEL,
    )
    assert loaded is not None, (
        "向量檔與對照表不同步或不存在，請執行 scripts/build_symptom_vectors.py"
    )
    assert loaded.terms == table.terms
