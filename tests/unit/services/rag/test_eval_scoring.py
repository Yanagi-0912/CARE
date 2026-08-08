from langchain_core.documents import Document

from app.services.rag.eval_scoring import (
    EvalCase,
    is_doc_retrieval_hit,
    score_case_retrieval,
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
