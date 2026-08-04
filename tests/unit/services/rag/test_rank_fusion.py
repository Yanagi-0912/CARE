import pytest
from langchain_core.documents import Document

from app.services.rag.rank_fusion import (
    DEFAULT_RRF_K,
    default_doc_key,
    reciprocal_rank_fusion,
)


def _doc(doc_id: str, text: str = "", score: float | None = None) -> Document:
    metadata: dict = {"id": doc_id}
    if score is not None:
        metadata["score"] = score
    return Document(page_content=text or f"content-{doc_id}", metadata=metadata)


def _ids(docs: list[Document]) -> list[str]:
    return [d.metadata["id"] for d in docs]


# ── 分數計算 ────────────────────────────────────────────────────────


def test_default_k_is_60():
    assert DEFAULT_RRF_K == 60


def test_score_matches_rrf_formula():
    fused = reciprocal_rank_fusion(
        [("vector", [_doc("a"), _doc("b")]), ("text", [_doc("b")])], k=60
    )
    by_id = {d.metadata["id"]: d for d in fused}

    # a 只在 vector 第 1 名
    assert by_id["a"].metadata["rrf_score"] == pytest.approx(1 / 61)
    # b 在 vector 第 2、text 第 1
    assert by_id["b"].metadata["rrf_score"] == pytest.approx(1 / 62 + 1 / 61)


def test_doc_found_by_both_retrievers_outranks_single_hit():
    """兩邊都命中的應該浮上來 —— 這是 RRF 的核心行為。"""
    fused = reciprocal_rank_fusion(
        [
            ("vector", [_doc("only-vector"), _doc("both")]),
            ("text", [_doc("both"), _doc("only-text")]),
        ]
    )
    assert _ids(fused)[0] == "both"


def test_doc_missing_from_vector_still_enters_the_pool():
    """
    hybrid 的重點：純向量撈不到的文件至少要進候選池，
    後面才輪得到 reranker 把它拉上來。
    """
    fused = reciprocal_rank_fusion(
        [
            ("vector", [_doc("v1"), _doc("v2"), _doc("v3")]),
            ("text", [_doc("exact-term-hit")]),
        ]
    )
    assert "exact-term-hit" in _ids(fused)


def test_smaller_k_amplifies_rank_differences():
    """k 越小，名次差距的影響越大。"""
    lists = [("vector", [_doc("first"), _doc("second")])]
    flat = reciprocal_rank_fusion(lists, k=60)
    sharp = reciprocal_rank_fusion(lists, k=1)

    flat_gap = flat[0].metadata["rrf_score"] - flat[1].metadata["rrf_score"]
    sharp_gap = sharp[0].metadata["rrf_score"] - sharp[1].metadata["rrf_score"]
    assert sharp_gap > flat_gap


def test_rejects_non_positive_k():
    with pytest.raises(ValueError, match="must be positive"):
        reciprocal_rank_fusion([("vector", [_doc("a")])], k=0)
    with pytest.raises(ValueError, match="must be positive"):
        reciprocal_rank_fusion([("vector", [_doc("a")])], k=-60)


# ── 去重與穩定性 ────────────────────────────────────────────────────


def test_deduplicates_by_id():
    fused = reciprocal_rank_fusion(
        [("vector", [_doc("same")]), ("text", [_doc("same")])]
    )
    assert len(fused) == 1
    assert fused[0].metadata["retrievers"] == ["vector", "text"]


def test_falls_back_to_content_key_when_id_missing():
    a = Document(page_content="一樣的內容", metadata={})
    b = Document(page_content="一樣的內容", metadata={})
    fused = reciprocal_rank_fusion([("vector", [a]), ("text", [b])])
    assert len(fused) == 1


def test_ties_keep_first_seen_order():
    """同分時順序必須穩定，否則測試與線上行為都不可重現。"""
    fused = reciprocal_rank_fusion(
        [("vector", [_doc("x")]), ("text", [_doc("y")])]
    )
    assert fused[0].metadata["rrf_score"] == fused[1].metadata["rrf_score"]
    assert _ids(fused) == ["x", "y"]


def test_same_retriever_repeating_a_doc_keeps_best_rank():
    fused = reciprocal_rank_fusion(
        [("vector", [_doc("dup"), _doc("other"), _doc("dup")])]
    )
    by_id = {d.metadata["id"]: d for d in fused}
    assert by_id["dup"].metadata["rrf_ranks"]["vector"] == 1


# ── metadata ────────────────────────────────────────────────────────


def test_score_is_overwritten_with_rrf_score_for_downstream_reranker():
    """
    VectorScoreReranker 是照 metadata["score"] 排序的。BM25 分數與 cosine
    尺度不可比，若原封不動留著會排錯，所以 score 必須換成融合後的分數。
    """
    fused = reciprocal_rank_fusion(
        [
            ("vector", [_doc("a", score=0.83)]),
            ("text", [_doc("a", score=12.7)]),
        ]
    )
    doc = fused[0]
    assert doc.metadata["score"] == doc.metadata["rrf_score"]
    assert doc.metadata["score"] < 1  # 不再是那個 12.7
    # 原始分數保留下來供除錯
    assert doc.metadata["vector_score"] == 0.83
    assert doc.metadata["text_score"] == 12.7


def test_records_ranks_and_sources():
    fused = reciprocal_rank_fusion(
        [
            ("vector", [_doc("a"), _doc("target")]),
            ("text", [_doc("target")]),
        ]
    )
    target = next(d for d in fused if d.metadata["id"] == "target")
    assert target.metadata["rrf_ranks"] == {"vector": 2, "text": 1}
    assert target.metadata["retrievers"] == ["vector", "text"]


def test_does_not_mutate_input_documents():
    original = _doc("a", score=0.9)
    reciprocal_rank_fusion([("vector", [original])])
    assert original.metadata == {"id": "a", "score": 0.9}


def test_preserves_unrelated_metadata():
    doc = Document(
        page_content="內容",
        metadata={"id": "a", "url": "https://x.example", "source_name": "衛福部"},
    )
    fused = reciprocal_rank_fusion([("vector", [doc])])
    assert fused[0].metadata["url"] == "https://x.example"
    assert fused[0].metadata["source_name"] == "衛福部"


# ── 邊界 ────────────────────────────────────────────────────────────


def test_limit_truncates_after_sorting():
    fused = reciprocal_rank_fusion(
        [
            ("vector", [_doc("a"), _doc("b"), _doc("c")]),
            ("text", [_doc("c")]),
        ],
        limit=2,
    )
    assert _ids(fused) == ["c", "a"]


def test_empty_and_missing_lists():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([("vector", []), ("text", [])]) == []
    assert reciprocal_rank_fusion([("vector", None), ("text", [_doc("a")])]) != []


def test_default_doc_key_prefers_id_over_content():
    assert default_doc_key(_doc("the-id", text="內容")) == "the-id"
    assert default_doc_key(Document(page_content="內容", metadata={})) == "content:內容"
    # 空白的 id 視為沒有 id
    assert default_doc_key(
        Document(page_content="內容", metadata={"id": "   "})
    ) == "content:內容"
