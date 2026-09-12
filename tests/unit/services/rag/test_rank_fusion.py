import pytest
from langchain_core.documents import Document

from app.services.rag.rank_fusion import (
    DEFAULT_RRF_K,
    convex_combination_fusion,
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


# ── 凸組合 ──────────────────────────────────────────────────────────


def test_convex_score_is_weighted_sum_of_normalized_scores():
    """min-max 正規化後加權相加；只被單腿撈到的另一腿記 0。"""
    fused = convex_combination_fusion(
        [
            ("vector", [_doc("a", score=0.9), _doc("b", score=0.5)]),
            ("text", [_doc("b", score=12.0), _doc("c", score=4.0)]),
        ],
        weights={"vector": 0.5, "text": 0.5},
    )
    by_id = {d.metadata["id"]: d for d in fused}

    # vector 腿：0.9→1.0、0.5→0.0；text 腿：12.0→1.0、4.0→0.0
    assert by_id["a"].metadata["score"] == pytest.approx(0.5 * 1.0)
    assert by_id["b"].metadata["score"] == pytest.approx(0.5 * 0.0 + 0.5 * 1.0)
    assert by_id["c"].metadata["score"] == pytest.approx(0.0)


def test_convex_normalizes_across_incomparable_scales():
    """
    BM25 的 12.7 不會壓過 cosine 的 0.83——這正是不能直接相加分數、
    而必須先正規化的理由。
    """
    fused = convex_combination_fusion(
        [
            ("vector", [_doc("cosine-top", score=0.83), _doc("cosine-low", score=0.10)]),
            ("text", [_doc("bm25-top", score=12.7), _doc("bm25-low", score=0.4)]),
        ]
    )
    by_id = {d.metadata["id"]: d for d in fused}
    assert by_id["cosine-top"].metadata["score"] == pytest.approx(
        by_id["bm25-top"].metadata["score"]
    )


def test_convex_weight_shifts_the_ranking():
    """alpha 真的會換名次，否則掃描沒有意義。"""
    lists = [
        ("vector", [_doc("v-only", score=0.9)]),
        ("text", [_doc("t-only", score=9.0)]),
    ]
    vector_heavy = convex_combination_fusion(lists, weights={"vector": 0.9, "text": 0.1})
    text_heavy = convex_combination_fusion(lists, weights={"vector": 0.1, "text": 0.9})

    assert _ids(vector_heavy)[0] == "v-only"
    assert _ids(text_heavy)[0] == "t-only"


def test_convex_alpha_one_reduces_to_vector_only_ordering():
    """端點行為要可讀：alpha=1 等於純向量排序，text 腿只貢獻候選。"""
    fused = convex_combination_fusion(
        [
            ("vector", [_doc("v1", score=0.9), _doc("v2", score=0.2)]),
            ("text", [_doc("t1", score=9.0)]),
        ],
        weights={"vector": 1.0, "text": 0.0},
    )
    assert _ids(fused)[0] == "v1"
    assert "t1" in _ids(fused)  # 仍在候選池裡，留給 reranker


def test_convex_rewards_scoring_high_in_both_legs_not_merely_appearing_in_both():
    """
    與 RRF 最實質的行為差異（模組 docstring 取捨 3）：RRF 只要多出現在一腿
    就一定加分，凸組合看的是分數，而 min-max 把每腿的最後一名壓成 0。

    這裡 both 在兩腿都緊咬第一名（正規化後各 ~0.94），合計勝過只在單腿
    拿滿分的文件（0.5）。注意「排第二」本身不夠——要兩腿的分數都逼近該腿
    頂端才會贏，這正是與 RRF 的差別。
    """
    fused = convex_combination_fusion(
        [
            (
                "vector",
                [
                    _doc("only-vector", score=0.95),
                    _doc("both", score=0.90),
                    _doc("v-tail", score=0.10),
                ],
            ),
            (
                "text",
                [
                    _doc("only-text", score=9.5),
                    _doc("both", score=9.0),
                    _doc("t-tail", score=1.0),
                ],
            ),
        ]
    )
    assert _ids(fused)[0] == "both"
    assert fused[0].metadata["score"] > 0.5


def test_convex_bottom_ranked_doc_contributes_nothing_from_that_leg():
    """
    取捨 3 的直接後果，刻意用測試釘住：「vector 墊底 + text 第一」與
    「vector 第一但 text 沒撈到」同分。看掃描結果時要記得這件事。
    """
    fused = convex_combination_fusion(
        [
            ("vector", [_doc("only-vector", score=0.9), _doc("both", score=0.8)]),
            ("text", [_doc("both", score=9.0), _doc("only-text", score=8.0)]),
        ]
    )
    by_id = {d.metadata["id"]: d for d in fused}
    assert by_id["both"].metadata["score"] == pytest.approx(
        by_id["only-vector"].metadata["score"]
    )


def test_convex_single_document_leg_is_treated_as_that_legs_top():
    """
    只回傳一筆時 min==max，min-max 無定義。記 1.0 而不是 0.0——否則
    「這一腿唯一撈到的那篇」會被當成該腿最差的一篇。
    """
    fused = convex_combination_fusion(
        [("text", [_doc("lonely", score=3.3)])],
        weights={"text": 1.0},
    )
    assert fused[0].metadata["score"] == pytest.approx(1.0)
    assert fused[0].metadata["text_norm"] == pytest.approx(1.0)


def test_convex_all_equal_scores_give_no_discrimination():
    fused = convex_combination_fusion(
        [("vector", [_doc("a", score=0.5), _doc("b", score=0.5)])],
        weights={"vector": 1.0},
    )
    assert [d.metadata["score"] for d in fused] == [
        pytest.approx(1.0),
        pytest.approx(1.0),
    ]
    assert _ids(fused) == ["a", "b"]  # 同分時維持先出現者在前


def test_convex_missing_score_metadata_falls_back_to_zero():
    """retriever 保證會寫 score，這裡驗的是防禦路徑不會讓整腿失效。"""
    no_score = Document(page_content="無分數", metadata={"id": "no-score"})
    fused = convex_combination_fusion(
        [("vector", [_doc("scored", score=0.9), no_score])],
        weights={"vector": 1.0},
    )
    assert _ids(fused) == ["scored", "no-score"]


def test_convex_weights_are_normalized_and_recorded():
    fused = convex_combination_fusion(
        [("vector", [_doc("a", score=1.0)]), ("text", [_doc("a", score=1.0)])],
        weights={"vector": 3.0, "text": 1.0},
    )
    assert fused[0].metadata["fusion_weights"] == {"vector": 0.75, "text": 0.25}


def test_convex_rejects_missing_and_invalid_weights():
    lists = [("vector", [_doc("a")]), ("text", [_doc("b")])]
    with pytest.raises(ValueError, match="missing fusion weight"):
        convex_combination_fusion(lists, weights={"vector": 1.0})
    with pytest.raises(ValueError, match="non-negative"):
        convex_combination_fusion(lists, weights={"vector": -1.0, "text": 2.0})
    with pytest.raises(ValueError, match="sum to zero"):
        convex_combination_fusion(lists, weights={"vector": 0.0, "text": 0.0})


def test_convex_defaults_to_equal_weights():
    fused = convex_combination_fusion(
        [("vector", [_doc("a", score=1.0)]), ("text", [_doc("b", score=1.0)])]
    )
    assert fused[0].metadata["fusion_weights"] == {"vector": 0.5, "text": 0.5}


def test_convex_metadata_matches_rrf_shape():
    """兩種融合的下游程式碼是同一份，共用的鍵必須同名同義。"""
    lists = [("vector", [_doc("a", score=0.9)]), ("text", [_doc("a", score=9.0)])]
    rrf = reciprocal_rank_fusion(lists)[0].metadata
    convex = convex_combination_fusion(lists)[0].metadata

    for key in ("score", "fusion", "fusion_score", "fusion_ranks", "retrievers"):
        assert key in rrf and key in convex
    assert rrf["fusion"] == "rrf"
    assert convex["fusion"] == "convex"
    assert convex["score"] == convex["fusion_score"]
    # 原始分數同樣保留供除錯
    assert convex["vector_score"] == 0.9
    assert convex["text_score"] == 9.0


def test_convex_limit_truncates_after_sorting():
    fused = convex_combination_fusion(
        [
            # b 在 vector 排中間（norm 0.5）、在 text 是唯一一筆（norm 1.0），
            # 合計 0.75 > a 的 0.5，所以 limit=1 要留下 b 而不是原本第一順位的 a。
            (
                "vector",
                [_doc("a", score=0.9), _doc("b", score=0.5), _doc("c", score=0.1)],
            ),
            ("text", [_doc("b", score=9.0)]),
        ],
        limit=1,
    )
    assert _ids(fused) == ["b"]


def test_convex_does_not_mutate_input_documents():
    original = _doc("a", score=0.9)
    convex_combination_fusion([("vector", [original])])
    assert original.metadata == {"id": "a", "score": 0.9}


def test_convex_empty_and_missing_lists():
    assert convex_combination_fusion([]) == []
    assert convex_combination_fusion([("vector", []), ("text", [])]) == []
    assert convex_combination_fusion([("vector", None), ("text", [_doc("a")])]) != []
