"""問卷素材腳本的純函式：選題、版本輪換、路徑分類與兩兩比較。

兩兩比較的分層是這支腳本的重點——問卷要挑「使用者看得出差別」的題目，
所以「路不同」（知識庫／網搜／查不到）、「同一條路但依據的來源不同」、
「只有措辭不同」必須分得開；最後一層是 LLM 本來就有的隨機性。
"""

import pytest

from app.services.rag.eval_scoring import EvalCase
from scripts.rag_survey_dump import (
    RunResult,
    classify_outcome,
    compare_pair,
    display_text,
    parse_stage_message,
    rotated,
    run_labels,
    select_cases,
    summarize,
)


def _case(case_id, route="kb", verdict=""):
    return EvalCase(id=case_id, query=f"q-{case_id}", route=route, expected_verdict=verdict)


def _result(run, case_id, *, outcome="kb", sources=(), error=None, seconds=1.0):
    return RunResult(
        run=run,
        variant=run.split("#")[0],
        case_id=case_id,
        query="q",
        route="kb",
        outcome=outcome,
        sources=[{"index": i + 1, "label": "src", "url": u} for i, u in enumerate(sources)],
        error=error,
        seconds=seconds,
    )


def test_select_cases_drops_verdict_and_refuse():
    cases = [
        _case("kb-1"),
        _case("web-1", route="web"),
        _case("verdict-1", route="web", verdict="錯誤"),
        _case("refuse-1", route="refuse"),
    ]
    assert [c.id for c in select_cases(cases)] == ["kb-1", "web-1"]


def test_select_cases_rejects_excluded_ids():
    cases = [_case("kb-1"), _case("verdict-1", route="web", verdict="錯誤")]
    with pytest.raises(ValueError, match="verdict-1"):
        select_cases(cases, ids=["verdict-1"])


def test_select_cases_keeps_only_requested_ids():
    cases = [_case("kb-1"), _case("kb-2"), _case("kb-3")]
    assert [c.id for c in select_cases(cases, ids=["kb-3", "kb-1"])] == ["kb-1", "kb-3"]


def test_run_labels_number_repeats_as_control_runs():
    assert run_labels(["A", "C", "C"]) == ["A", "C", "C#2"]


def test_rotation_gives_every_run_the_first_slot():
    labels = ["A", "B", "C"]
    assert [rotated(labels, i)[0] for i in range(4)] == ["A", "B", "C", "A"]
    assert rotated(labels, 1) == ["B", "C", "A"]


def test_parse_stage_message():
    parsed = parse_stage_message("stage=rag_answer ms=8123 path=web_crag_reject top_rerank=0.21")
    assert parsed == {
        "stage": "rag_answer",
        "ms": "8123",
        "path": "web_crag_reject",
        "top_rerank": "0.21",
    }
    assert parse_stage_message("rag_fail code=KB_EMPTY") is None


def test_classify_outcome():
    assert classify_outcome("[RAG_ERR:MODEL_REFUSE] 抱歉", "kb_model_refuse") == "no_answer"
    assert classify_outcome("[RAG_ERR:WEB_EMPTY] 抱歉", "web_crag_reject") == "no_answer"
    assert classify_outcome("網路上的答案", "web_crag_reject") == "web"
    assert classify_outcome("知識庫的答案", "kb") == "kb"
    assert classify_outcome("知識庫的答案", "kb_crag_degraded") == "kb"


def test_display_text_strips_fail_prefix_only():
    assert display_text("[RAG_ERR:KB_EMPTY] 找不到相關資料") == "找不到相關資料"
    assert display_text("一般答案 [1]") == "一般答案 [1]"


def test_compare_pair_layers():
    assert compare_pair(_result("A", "k", outcome="kb"), _result("B", "k", outcome="web")) == "outcome"
    assert compare_pair(_result("A", "k", sources=["u1"]), _result("B", "k", sources=["u2"])) == "sources"
    assert compare_pair(_result("A", "k", sources=["u1"]), _result("B", "k", sources=["u1"])) == "wording"
    assert compare_pair(_result("A", "k", error="boom"), _result("B", "k")) == "error"


def test_citation_order_is_not_a_source_difference():
    left = _result("A", "k", sources=["u1", "u2"])
    right = _result("B", "k", sources=["u2", "u1"])
    assert compare_pair(left, right) == "wording"


def test_summarize_pairs_outcomes_and_latency():
    results = [
        _result("A", "k1", outcome="kb", seconds=2.0),
        _result("B", "k1", outcome="web", seconds=10.0),
        _result("A", "k2", sources=["u"], seconds=4.0),
        _result("B", "k2", sources=["u"], seconds=6.0),
        _result("A", "k3", error="boom", seconds=30.0),
        _result("B", "k3", seconds=5.0),
    ]
    summary = summarize(results, ["A", "B"])

    pair = summary["pairs"][0]
    assert pair["pair"] == "A vs B"
    assert pair["outcome"] == ["k1"]
    assert pair["wording"] == ["k2"]
    assert pair["error"] == ["k3"]

    # 出錯那筆不算進等待時間：30 秒是例外路徑，不是使用者等到答案的時間
    assert summary["latency_seconds"]["A"]["median"] == 3.0
    assert summary["latency_seconds"]["A"]["max"] == 4.0
    assert summary["latency_seconds"]["B"]["by_outcome"]["web"] == {"n": 1, "median": 10.0}
