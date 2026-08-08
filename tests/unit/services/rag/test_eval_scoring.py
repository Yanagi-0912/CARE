import math

import pytest
from langchain_core.documents import Document

from app.services.rag.eval_scoring import (
    CaseResult,
    EvalCase,
    doc_relevances,
    is_doc_retrieval_hit,
    mrr,
    ndcg_at_k,
    score_case_retrieval,
    summarize_results,
    titles_from_docs,
)


def _doc(*, title=None, url=None, source=None, content="內容"):
    return Document(
        page_content=content,
        metadata={"original_title": title, "url": url, "source_name": source},
    )


def test_titles_from_docs_skips_blank():
    docs = [_doc(title="捍「胃」健康"), _doc(title=None), _doc(title="  ")]
    assert titles_from_docs(docs) == ["捍「胃」健康"]


def test_is_doc_retrieval_hit_matches_title_when_url_missing():
    docs = [_doc(title="捍「胃」健康 過年聚餐用公筷", url=None)]
    assert is_doc_retrieval_hit(
        docs,
        expected_url_substrings=["pid=19853"],
        expected_source_substrings=[],
        expected_content_substrings=[],
        expected_title_substrings=["捍「胃」健康"],
    )


def test_score_case_retrieval_scores_title_only_case():
    case = EvalCase(
        id="kb-900",
        query="幽門螺旋桿菌檢測",
        route="kb",
        expected_title_substrings=["捍「胃」健康"],
    )
    result = score_case_retrieval(case, [_doc(title="捍「胃」健康 過年聚餐用公筷")])
    assert result.skipped is False
    assert result.retrieval_hit is True
    assert result.retrieved_titles == ["捍「胃」健康 過年聚餐用公筷"]


def test_mrr_uses_first_relevant_rank():
    assert mrr([1, 0, 0]) == 1.0
    assert mrr([0, 1, 0]) == 0.5
    assert mrr([0, 0, 1]) == pytest.approx(1 / 3)
    assert mrr([0, 0, 0]) == 0.0


def test_ndcg_at_k_rewards_earlier_position():
    assert ndcg_at_k([1, 0, 0, 0, 0], 5) == 1.0
    later = ndcg_at_k([0, 0, 1, 0, 0], 5)
    assert later == pytest.approx(1 / math.log2(4))
    assert later < 1.0


def test_ndcg_at_k_returns_zero_when_no_relevant():
    assert ndcg_at_k([0, 0, 0], 5) == 0.0


def test_ndcg_at_k_numerator_ignores_docs_beyond_k():
    """gains（分子）只看前 k 篇：全零時分子恆為 0，這裡不足以分辨 IDCG 口徑對錯。"""
    assert ndcg_at_k([0, 0, 0, 0, 0, 1], 5) == 0.0


def test_ndcg_at_k_idcg_uses_full_list_order_not_truncated_order():
    """IDCG 必須用「整份取回清單重排後」取前 k，而非「先截斷成前 k 再排」。

    relevances = [0, 1, 0, 0, 0, 1]，k=5：
    - gains（分子，前 5 篇）= [0, 1, 0, 0, 0] → DCG = 1/log2(3)（唯一命中在第 2 名）
    - 正確 IDCG：對「整份 6 篇」重排 = [1, 1, 0, 0, 0, 0]，取前 5 = [1, 1, 0, 0, 0]
      → IDCG = 1/log2(2) + 1/log2(3)，因為第 6 名那筆命中也被排進理想序的前段
    - 錯誤 IDCG（先截斷再排，Task 3 review 抓到的 bug 型態）：
      只重排前 5 篇 [0, 1, 0, 0, 0] → [1, 0, 0, 0, 0] → IDCG = 1/log2(2)
      算出 ndcg ≈ 0.631，而非正確的 ≈ 0.387 —— 兩者差異明顯，可分辨實作對錯。
    """
    relevances = [0, 1, 0, 0, 0, 1]
    numerator = 1 / math.log2(3)
    correct_idcg = 1 / math.log2(2) + 1 / math.log2(3)
    expected = numerator / correct_idcg
    result = ndcg_at_k(relevances, 5)
    assert result == pytest.approx(expected)
    # 確認不是誤把 IDCG 算成「先截斷再排」的錯誤值
    wrong_idcg = 1 / math.log2(2)
    assert result != pytest.approx(numerator / wrong_idcg)


def test_doc_relevances_marks_each_doc_independently():
    case = EvalCase(
        id="kb-901",
        query="q",
        route="kb",
        expected_title_substrings=["捍「胃」健康"],
    )
    docs = [_doc(title="無關文章"), _doc(title="捍「胃」健康 過年聚餐用公筷")]
    assert doc_relevances(case, docs) == [0, 1]


def test_summarize_results_averages_only_scored_cases():
    results = [
        CaseResult(
            id="a", query="q", route="kb", skipped=False, retrieval_hit=True,
            retrieved_urls=[], mrr=1.0, ndcg_at_5=1.0,
        ),
        CaseResult(
            id="b", query="q", route="kb", skipped=False, retrieval_hit=False,
            retrieved_urls=[], mrr=0.0, ndcg_at_5=0.0,
        ),
        CaseResult(
            id="c", query="q", route="web", skipped=True, retrieval_hit=None,
            retrieved_urls=[],
        ),
    ]
    summary = summarize_results(results)
    assert summary.scored_cases == 2
    assert summary.mean_mrr == 0.5
    assert summary.mean_ndcg_at_5 == 0.5
    assert summary.skipped_ids == ["c"]
