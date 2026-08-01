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
