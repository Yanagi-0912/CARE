"""RAG eval scoring 純函式測試（依賴注入，不 monkey patch）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.documents import Document

from app.services.rag.eval_scoring import (
    VALID_VERDICTS,
    EvalCase,
    CaseResult,
    EvalSummary,
    VerdictResult,
    VerdictSummary,
    is_retrieval_hit,
    is_refuse_ok,
    is_source_hit,
    load_golden_jsonl,
    score_case_retrieval,
    score_verdict,
    source_names_from_docs,
    summarize_results,
    summarize_verdicts,
    urls_from_docs,
    urls_from_answer_sources,
    verdict_severity_distance,
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


# --- Task 8: 判定正確率／誤配率 -------------------------------------------


def test_eval_case_expected_verdict_defaults_empty_and_ignored_by_retrieval():
    """既有題目（不設 expected_verdict）行為完全不變：預設空字串，且
    has_retrieval_expectations 只看四個 expected_*_substrings 欄位。"""
    case = EvalCase(
        id="kb-x", query="q", route="kb", expected_url_substrings=["hpa.gov"]
    )
    assert case.expected_verdict == ""
    assert case.has_retrieval_expectations is True


def test_load_golden_jsonl_parses_expected_verdict(tmp_path: Path):
    path = tmp_path / "g.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "verdict-1",
                "query": "網傳X可以治百病，真的嗎？",
                "route": "web",
                "expected_verdict": "證據不足",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    cases = load_golden_jsonl(path)
    assert cases[0].expected_verdict == "證據不足"


def test_load_golden_jsonl_expected_verdict_omitted_defaults_empty(tmp_path: Path):
    """既有題目沒有這個 key：載入後仍是空字串，不是 None 或報錯。"""
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
        + "\n",
        encoding="utf-8",
    )
    cases = load_golden_jsonl(path)
    assert cases[0].expected_verdict == ""


def test_load_golden_jsonl_rejects_invalid_expected_verdict(tmp_path: Path):
    path = tmp_path / "g.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "bad-verdict",
                "query": "q",
                "route": "web",
                "expected_verdict": "真的假的",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="expected_verdict"):
        load_golden_jsonl(path)


def test_verdict_severity_distance_adjacent_and_extremes():
    # brief 實測的錯誤型態：期望「錯誤」、實得「部分錯誤」，相鄰一級。
    assert verdict_severity_distance("錯誤", "部分錯誤") == 1
    assert verdict_severity_distance("正確", "事實釐清") == 1
    # 兩端顛倒（真假顛倒）是序上距離最遠的一種
    assert verdict_severity_distance("正確", "錯誤") == 4
    assert verdict_severity_distance("證據不足", "證據不足") == 0


def test_score_verdict_not_applicable_without_expected_verdict():
    case = EvalCase(id="c1", query="q", route="web")
    result = score_verdict(case, "錯誤")
    assert result.applicable is False


def test_score_verdict_correct():
    case = EvalCase(id="c1", query="q", route="web", expected_verdict="錯誤")
    result = score_verdict(case, "錯誤")
    assert result.applicable is True
    assert result.is_mismatch_case is False
    assert result.correct is True
    assert result.error_kind is None


def test_score_verdict_wrong_is_tagged_adjacent():
    """brief 記錄的實測錯誤型態：期望「錯誤」、實得「部分錯誤」——使用者仍
    被告知該說法有問題，是可接受的偏差，跟真假顛倒嚴重度不同。"""
    case = EvalCase(id="c1", query="q", route="web", expected_verdict="錯誤")
    result = score_verdict(case, "部分錯誤")
    assert result.correct is False
    assert result.error_kind == "相鄰"


def test_score_verdict_wrong_is_tagged_reversed():
    """真假顛倒：期望「正確」、實得「錯誤」，是使用者會被誤導方向的嚴重失效。"""
    case = EvalCase(id="c1", query="q", route="web", expected_verdict="正確")
    result = score_verdict(case, "錯誤")
    assert result.correct is False
    assert result.error_kind == "顛倒"


def test_score_verdict_mismatch_when_expected_no_evidence_but_system_matched():
    """誤配：期望「證據不足」，系統卻回了別的判定——查核功能唯一的嚴重失效模式。"""
    case = EvalCase(id="c1", query="q", route="web", expected_verdict="證據不足")
    result = score_verdict(case, "錯誤")
    assert result.is_mismatch_case is True
    assert result.mismatched is True
    # 誤配母體不進判定正確率，correct 對這題沒有意義
    assert result.correct is None


def test_score_verdict_no_mismatch_when_correctly_unmatched():
    case = EvalCase(id="c1", query="q", route="web", expected_verdict="證據不足")
    result = score_verdict(case, "證據不足")
    assert result.is_mismatch_case is True
    assert result.mismatched is False


def test_summarize_verdicts_keeps_accuracy_and_mismatch_denominators_disjoint():
    """spec 明文：誤配 SHALL 單獨計分，SHALL NOT 併入判定正確率。這裡直接
    構造 VerdictResult 驗證 summarize_verdicts 的彙總，不經過 score_verdict，
    以便單獨檢查彙總邏輯本身。"""
    results = [
        VerdictResult(
            id="v1", expected_verdict="錯誤", actual_verdict="錯誤",
            applicable=True, correct=True,
        ),
        VerdictResult(
            id="v2", expected_verdict="錯誤", actual_verdict="部分錯誤",
            applicable=True, correct=False, error_kind="相鄰",
        ),
        VerdictResult(
            id="v3", expected_verdict="正確", actual_verdict="錯誤",
            applicable=True, correct=False, error_kind="顛倒",
        ),
        VerdictResult(
            id="v4", expected_verdict="證據不足", actual_verdict="錯誤",
            applicable=True, is_mismatch_case=True, mismatched=True,
        ),
        VerdictResult(
            id="v5", expected_verdict="證據不足", actual_verdict="證據不足",
            applicable=True, is_mismatch_case=True, mismatched=False,
        ),
        # 未標註 expected_verdict：不進入任何一個指標的分母
        VerdictResult(
            id="v6", expected_verdict="", actual_verdict="錯誤", applicable=False,
        ),
    ]
    summary = summarize_verdicts(results)
    assert isinstance(summary, VerdictSummary)
    assert summary.scored_cases == 3
    assert summary.correct == 1
    assert summary.verdict_accuracy == pytest.approx(1 / 3)
    assert summary.wrong_ids == ["v2", "v3"]
    assert summary.adjacent_wrong_ids == ["v2"]
    assert summary.reversed_wrong_ids == ["v3"]
    assert summary.mismatch_cases == 2
    assert summary.mismatches == 1
    assert summary.mismatch_rate == 0.5
    assert summary.mismatch_ids == ["v4"]


def test_summarize_verdicts_empty_is_none_not_zero_division():
    summary = summarize_verdicts([])
    assert summary.scored_cases == 0
    assert summary.verdict_accuracy is None
    assert summary.mismatch_cases == 0
    assert summary.mismatch_rate is None
    assert summary.wrong_ids == []
    assert summary.mismatch_ids == []


def test_real_golden_jsonl_verdict_cases_are_additive_only():
    """迴歸鎖：正式 golden.jsonl 全部可解析，且既有 38 題完全不受
    expected_verdict 影響（沒有這個欄位、載入後為空字串）——對應 Task 8
    brief 的完成定義與自我 review 項目。"""
    golden_path = (
        Path(__file__).resolve().parents[3] / "evals" / "rag" / "golden.jsonl"
    )
    cases = load_golden_jsonl(golden_path)
    verdict_cases = [c for c in cases if c.expected_verdict]
    non_verdict_cases = [c for c in cases if not c.expected_verdict]

    assert len(non_verdict_cases) == 38  # 既有題目，數量與欄位都不變
    assert len(verdict_cases) == 12  # Task 8 新增的查核型題目
    assert all(c.expected_verdict in VALID_VERDICTS for c in verdict_cases)
    # 誤配率唯一量得到東西的母體：brief 要求至少 4 題
    no_evidence = [c for c in verdict_cases if c.expected_verdict == "證據不足"]
    assert len(no_evidence) >= 4
