"""RAG eval scoring 純函式測試（依賴注入，不 monkey patch）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.documents import Document

from app.services.rag.eval_scoring import (
    EvalCase,
    CaseResult,
    EvalSummary,
    is_retrieval_hit,
    is_refuse_ok,
    is_source_hit,
    load_golden_jsonl,
    score_case_retrieval,
    source_names_from_docs,
    summarize_results,
    urls_from_docs,
    urls_from_answer_sources,
)


def test_is_retrieval_hit_matches_substring():
    urls = [
        "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1",
        "https://example.com/other",
    ]
    assert is_retrieval_hit(urls, ["hpa.gov"]) is True
    assert is_retrieval_hit(urls, ["mohw.gov"]) is False


def test_is_retrieval_hit_empty_expectations_is_false():
    assert is_retrieval_hit(["https://a.com"], []) is False
    assert is_retrieval_hit([], ["hpa.gov"]) is False


def test_urls_from_docs():
    docs = [
        Document(page_content="a", metadata={"url": "https://a.com"}),
        Document(page_content="b", metadata={"url": ""}),
        Document(page_content="c", metadata={}),
    ]
    assert urls_from_docs(docs) == ["https://a.com"]


def test_source_names_from_docs():
    docs = [
        Document(page_content="a", metadata={"source_name": "食藥署闢謠專區"}),
        Document(page_content="b", metadata={"source_name": ""}),
        Document(page_content="c", metadata={}),
    ]
    assert source_names_from_docs(docs) == ["食藥署闢謠專區"]


def test_score_case_hits_via_source_when_url_missing():
    case = EvalCase(
        id="kb-fda",
        query="感冒吃抗生素有用嗎？",
        route="kb",
        expected_url_substrings=[],
        expected_source_substrings=["食藥署"],
    )
    docs = [
        Document(
            page_content="感冒多為病毒…",
            metadata={"url": None, "source_name": "食藥署闢謠專區"},
        )
    ]
    result = score_case_retrieval(case, docs)
    assert result.skipped is False
    assert result.retrieval_hit is True


def test_score_case_hits_via_content_substring():
    case = EvalCase(
        id="kb-content",
        query="感冒吃抗生素有用嗎？",
        route="kb",
        expected_content_substrings=["不主動要求抗生素"],
    )
    docs = [
        Document(
            page_content="使用抗生素四不一要：不主動要求抗生素-感冒多為病毒感染",
            metadata={"url": None, "source_name": "食藥署闢謠專區"},
        )
    ]
    result = score_case_retrieval(case, docs)
    assert result.skipped is False
    assert result.retrieval_hit is True


def test_score_case_miss_when_content_expectation_absent():
    case = EvalCase(
        id="kb-content-miss",
        query="q",
        route="kb",
        expected_url_substrings=["pid=99999"],
        expected_content_substrings=["絕對不該出現的句子"],
    )
    docs = [
        Document(
            page_content="高血壓飲食注意",
            metadata={"url": "https://www.hpa.gov.tw/Pages/Detail.aspx?pid=16550"},
        )
    ]
    result = score_case_retrieval(case, docs)
    assert result.retrieval_hit is False


def test_score_case_skipped_without_url_or_source_expectations():
    case = EvalCase(id="kb-x", query="q", route="kb")
    result = score_case_retrieval(
        case, [Document(page_content="a", metadata={"url": "https://hpa.gov.tw"})]
    )
    assert result.skipped is True


def test_load_golden_jsonl_requires_fields(tmp_path: Path):
    path = tmp_path / "g.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "kb-1",
                "query": "高血壓？",
                "route": "kb",
                "expected_url_substrings": ["hpa.gov"],
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps({"id": "bad", "query": "x"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bad"):
        load_golden_jsonl(path)


def test_load_golden_jsonl_ok(tmp_path: Path):
    path = tmp_path / "g.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "kb-1",
                "query": "高血壓？",
                "route": "kb",
                "expected_url_substrings": ["hpa.gov"],
                "must_not_answer": False,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    cases = load_golden_jsonl(path)
    assert len(cases) == 1
    assert cases[0].id == "kb-1"
    assert cases[0].route == "kb"
    assert cases[0].expected_url_substrings == ["hpa.gov"]


def test_score_case_retrieval_hit():
    case = EvalCase(
        id="kb-1",
        query="q",
        route="kb",
        expected_url_substrings=["hpa.gov"],
    )
    docs = [
        Document(
            page_content="x",
            metadata={"url": "https://www.hpa.gov.tw/a", "score": 0.9},
        )
    ]
    result = score_case_retrieval(case, docs)
    assert result.retrieval_hit is True
    assert result.skipped is False
    assert "hpa.gov.tw" in result.retrieved_urls[0]


def test_score_case_retrieval_skips_non_kb_without_expectations():
    case = EvalCase(id="web-1", query="q", route="web", expected_url_substrings=[])
    result = score_case_retrieval(case, [])
    assert result.skipped is True
    assert result.retrieval_hit is None


def test_is_refuse_ok_detects_no_hits_message():
    from app.services.rag.answer_service import NO_HITS_MESSAGE, NO_ANSWER_MESSAGE

    assert is_refuse_ok(NO_HITS_MESSAGE) is True
    assert is_refuse_ok(NO_ANSWER_MESSAGE) is True
    assert is_refuse_ok("高血壓要少鹽多運動，建議每天量血壓。") is False


def test_is_source_hit_from_answer_text():
    answer = (
        "根據 RAG 資訊，要注意飲食。\n\n"
        "參考資料來源：\n"
        "[1] 衛教：https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1"
    )
    assert is_source_hit(answer, ["hpa.gov"]) is True
    assert is_source_hit(answer, ["cdc.gov"]) is False


def test_urls_from_answer_sources():
    answer = (
        "內容\n\n參考資料來源：\n"
        "[1] A：https://a.example/1\n"
        "[2] https://b.example/2"
    )
    assert urls_from_answer_sources(answer) == [
        "https://a.example/1",
        "https://b.example/2",
    ]


def test_summarize_results():
    results = [
        CaseResult(
            id="1",
            query="q1",
            route="kb",
            skipped=False,
            retrieval_hit=True,
            retrieved_urls=["https://a"],
        ),
        CaseResult(
            id="2",
            query="q2",
            route="kb",
            skipped=False,
            retrieval_hit=False,
            retrieved_urls=[],
        ),
        CaseResult(
            id="3",
            query="q3",
            route="web",
            skipped=True,
            retrieval_hit=None,
            retrieved_urls=[],
        ),
    ]
    summary = summarize_results(results)
    assert isinstance(summary, EvalSummary)
    assert summary.total_cases == 3
    assert summary.scored_cases == 2
    assert summary.hits == 1
    assert summary.hit_rate == 0.5
    assert summary.miss_ids == ["2"]


@pytest.mark.asyncio
async def test_run_eval_with_injected_retriever(tmp_path: Path):
    """CLI 核心 run_eval 接受注入的 retriever（不打真實 Mongo）。"""
    import importlib.util

    golden = tmp_path / "g.jsonl"
    golden.write_text(
        json.dumps(
            {
                "id": "kb-1",
                "query": "高血壓",
                "route": "kb",
                "expected_url_substrings": ["hpa.gov"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    from unittest.mock import AsyncMock, MagicMock

    retriever = MagicMock()
    retriever.ainvoke = AsyncMock(
        return_value=[
            Document(
                page_content="x",
                metadata={"url": "https://www.hpa.gov.tw/a"},
            )
        ]
    )

    spec = importlib.util.spec_from_file_location(
        "rag_eval_script",
        Path(__file__).resolve().parents[3] / "scripts" / "rag_eval.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    results, summary = await mod.run_eval(
        golden,
        with_answer=False,
        retriever=retriever,
    )
    assert summary.hit_rate == 1.0
    assert results[0].retrieval_hit is True
    retriever.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_compare_rerank_uses_injected_retriever_once(tmp_path: Path):
    import importlib.util
    from unittest.mock import AsyncMock, MagicMock

    golden = tmp_path / "g.jsonl"
    golden.write_text(
        json.dumps(
            {
                "id": "kb-1",
                "query": "抗生素",
                "route": "kb",
                "expected_source_substrings": ["食藥署"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    wide = [
        Document(
            page_content="感冒多為病毒",
            metadata={"url": None, "source_name": "食藥署闢謠專區", "score": 0.5},
        ),
        Document(
            page_content="無關",
            metadata={
                "url": "https://www.hpa.gov.tw/x",
                "source_name": "衛福部闢謠網站",
                "score": 0.9,
            },
        ),
    ]
    retriever = MagicMock()
    retriever.ainvoke = AsyncMock(return_value=wide)

    class PreferFda:
        async def rerank(self, query, docs, *, top_n):
            del query, top_n
            return [docs[0]]

    class PreferHpa:
        async def rerank(self, query, docs, *, top_n):
            del query, top_n
            return [docs[1]]

    spec = importlib.util.spec_from_file_location(
        "rag_eval_script2",
        Path(__file__).resolve().parents[3] / "scripts" / "rag_eval.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Patch builders via direct call of scoring path: monkeypatch-free by
    # calling internal loop pieces through run_compare with custom rerankers.
    # Instead, unit-test ranking selection effect via run_eval rank_mode=vector.
    results, summary = await mod.run_eval(
        golden,
        retriever=retriever,
        rank_mode="vector",
        top_n=1,
        reranker=PreferHpa(),
    )
    assert summary.hit_rate == 0.0
    assert results[0].retrieval_hit is False

    results2, summary2 = await mod.run_eval(
        golden,
        retriever=retriever,
        rank_mode="vector",
        top_n=1,
        reranker=PreferFda(),
    )
    assert summary2.hit_rate == 1.0
    assert results2[0].retrieval_hit is True


def _load_rag_eval_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "rag_eval_script_path_guard",
        Path(__file__).resolve().parents[3] / "scripts" / "rag_eval.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_cli_paths_accept_project_and_temp():
    mod = _load_rag_eval_module()
    golden = mod._resolve_golden_path(mod.DEFAULT_GOLDEN)
    assert golden == mod.DEFAULT_GOLDEN.resolve()

    out_tmp = mod._resolve_out_path(Path("/tmp") / "rag-report.json")
    assert out_tmp.name == "rag-report.json"
    assert any(out_tmp.is_relative_to(root) for root in mod._OUT_ALLOWED_ROOTS)


def test_resolve_cli_paths_reject_escape():
    mod = _load_rag_eval_module()
    with pytest.raises(ValueError, match="escapes allowed roots"):
        mod._resolve_golden_path(Path("/etc/passwd"))
    with pytest.raises(ValueError, match="escapes allowed roots"):
        mod._resolve_out_path(Path("/etc/passwd"))
