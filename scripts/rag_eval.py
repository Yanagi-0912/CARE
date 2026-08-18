#!/usr/bin/env python3
"""批次評測 RAG 檢索（可選答案層／精排 A/B）。

用法（專案根目錄）：
  python scripts/rag_eval.py
  python scripts/rag_eval.py --with-answer --out /tmp/rag-report.json
  python scripts/rag_eval.py --rank-mode vector --top-n 5
  python scripts/rag_eval.py --compare-rerank --out /tmp/rag-compare.json
  python scripts/rag_eval.py --fail-under 0.6
  python scripts/rag_eval.py --with-verdict
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from app.core.config import settings
from app.dependencies import (
    get_claim_verification_service,
    get_rag_answer_service,
    get_rag_retriever,
)
from app.services.rag.answer_service import dedup_ranked_docs
from app.services.rag.cohere_reranker import CohereReranker, VectorScoreReranker
from app.services.rag.eval_scoring import (
    VALID_VERDICTS,
    CaseResult,
    EvalCase,
    VerdictResult,
    VerdictSummary,
    answer_citation_count,
    load_golden_jsonl,
    score_case_retrieval,
    score_verdict,
    is_refuse_ok,
    is_source_hit,
    summarize_results,
    summarize_verdicts,
)


DEFAULT_GOLDEN = _PROJECT_ROOT / "evals" / "rag" / "golden.jsonl"
_OUT_ALLOWED_ROOTS = (
    _PROJECT_ROOT,
    Path(tempfile.gettempdir()).resolve(),
    Path("/tmp").resolve(),
)


def _resolve_under_allowed_roots(
    path: Path,
    allowed_roots: Sequence[Path],
) -> Path:
    """Resolve *path* and ensure it stays under one of *allowed_roots*.

    Prevents CLI path arguments (e.g. ``../../etc/passwd``) from escaping
    the intended filesystem sandbox before any read/write.
    """
    resolved = path.expanduser().resolve()
    for root in allowed_roots:
        root_resolved = root.resolve()
        if resolved == root_resolved or resolved.is_relative_to(root_resolved):
            return resolved
    roots = ", ".join(str(r.resolve()) for r in allowed_roots)
    raise ValueError(f"path escapes allowed roots ({roots}): {path}")


def _resolve_golden_path(path: Path) -> Path:
    return _resolve_under_allowed_roots(path, (_PROJECT_ROOT,))


def _resolve_out_path(path: Path) -> Path:
    return _resolve_under_allowed_roots(path, _OUT_ALLOWED_ROOTS)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CARE RAG eval harness")
    parser.add_argument(
        "--golden",
        type=Path,
        default=DEFAULT_GOLDEN,
        help=f"golden JSONL path (default: {DEFAULT_GOLDEN})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write full JSON report to this path",
    )
    parser.add_argument(
        "--with-answer",
        action="store_true",
        help="also call RagAnswerService.answer for source_hit / refuse_ok",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="exit 1 if retrieval hit_rate is below this threshold (0-1)",
    )
    parser.add_argument(
        "--split",
        type=str,
        default="",
        help="only evaluate cases with this split value (e.g. train)",
    )
    parser.add_argument(
        "--rank-mode",
        choices=("none", "vector", "cohere"),
        default="none",
        help=(
            "none=score all retrieved docs; "
            "vector=vector-score top-n; "
            "cohere=Cohere rerank top-n"
        ),
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=None,
        help=f"truncate after ranking (default: RAG_RERANK_TOP_N={settings.RAG_RERANK_TOP_N})",
    )
    parser.add_argument(
        "--compare-rerank",
        action="store_true",
        help="run vector vs cohere top-n side-by-side and print both hit_rates",
    )
    parser.add_argument(
        "--with-verdict",
        action="store_true",
        help=(
            "also verify cases with expected_verdict via "
            "ClaimVerificationService.verify() and print verdict accuracy / "
            "mismatch rate. Off by default: each case costs 3 LLM calls "
            "(normalize, identity check, reasoning rewrite), so this must "
            "stay opt-in or every routine `rag_eval.py` run gets slower and "
            "more expensive. Requires CLAIM_VERIFICATION_ENABLED=true."
        ),
    )
    return parser.parse_args(argv)


def _build_reranker(rank_mode: str):
    if rank_mode == "none":
        return None
    if rank_mode == "vector":
        return VectorScoreReranker()
    if not settings.COHERE_API_KEY:
        raise RuntimeError(
            "rank-mode=cohere requires COHERE_API_KEY in environment"
        )
    return CohereReranker(
        api_key=settings.COHERE_API_KEY,
        model=settings.COHERE_RERANK_MODEL,
        timeout_seconds=settings.COHERE_RERANK_TIMEOUT_SECONDS,
    )


async def _maybe_rank(docs, *, query: str, rank_mode: str, top_n: int, reranker):
    """鏡射 production `RagAnswerService._retrieve_and_rerank` 的行為。

    `rank_mode == "none"` 是刻意的裸檢索觀測（production 永遠有 reranker，
    這個分支不代表任何真實路徑），維持原始檢索順序不動、不套用去重。
    其餘 rank_mode（vector／cohere）都對應到「production 一定會呼叫某個
    reranker」的事實，因此鏡射同一套流程：先讓 reranker 對*完整*候選集
    排序（top_n=len(docs)，而非只要 top_n 筆），文章層級去重必須看過全部
    候選才能判斷哪些 chunk 因同文章擠壓而被排除；去重後才截斷到 top_n。
    """
    if rank_mode == "none" or reranker is None:
        return docs
    ranked = await reranker.rerank(query, docs, top_n=len(docs))
    deduped = dedup_ranked_docs(
        ranked, max_per_article=settings.RAG_RERANK_MAX_CHUNKS_PER_ARTICLE
    )
    return deduped[:top_n]


async def _eval_one(
    case: EvalCase,
    *,
    retriever,
    answer_service,
    with_answer: bool,
    rank_mode: str,
    top_n: int,
    reranker,
) -> CaseResult:
    needs_retrieval_score = case.route == "kb" and case.has_retrieval_expectations
    if not needs_retrieval_score and not with_answer:
        return score_case_retrieval(case, [])

    try:
        docs = await retriever.ainvoke(case.query)
        docs = await _maybe_rank(
            docs, query=case.query, rank_mode=rank_mode, top_n=top_n, reranker=reranker
        )
    except Exception as exc:  # noqa: BLE001 - 評測要收斂錯誤到報告
        return CaseResult(
            id=case.id,
            query=case.query,
            route=case.route,
            skipped=not needs_retrieval_score,
            retrieval_hit=False if needs_retrieval_score else None,
            retrieved_urls=[],
            error=f"retrieve: {type(exc).__name__}: {exc}",
            rank_mode=rank_mode,
        )

    result = score_case_retrieval(case, docs)
    result.rank_mode = rank_mode

    if not with_answer:
        return result

    try:
        answer = await answer_service.answer(case.query)
    except Exception as exc:  # noqa: BLE001
        result.error = f"answer: {type(exc).__name__}: {exc}"
        return result

    result.answer_preview = (answer or "")[:240]
    result.citation_count = answer_citation_count(answer)
    if case.must_not_answer or case.route == "refuse":
        result.refuse_ok = is_refuse_ok(answer)
    if case.route == "kb" and case.expected_url_substrings:
        result.source_hit = is_source_hit(answer, case.expected_url_substrings)
    return result


def _print_summary(label: str, golden: Path, summary, results, *, with_answer: bool) -> None:
    print(f"=== RAG Eval Summary ({label}) ===")
    print(f"golden: {golden}")
    print(f"total_cases: {summary.total_cases}")
    print(f"scored_cases: {summary.scored_cases}")
    print(f"hits: {summary.hits}")
    rate = summary.hit_rate
    print(f"hit_rate: {rate if rate is not None else 'n/a'}")

    def _fmt(value: Optional[float]) -> str:
        return f"{value:.3f}" if value is not None else "n/a"

    print(f"mean_mrr: {_fmt(summary.mean_mrr)}")
    print(f"mean_ndcg@5: {_fmt(summary.mean_ndcg_at_5)}")
    print(f"miss_ids: {summary.miss_ids}")
    print(f"skipped_ids: {summary.skipped_ids}")
    if summary.error_ids:
        print(f"error_ids: {summary.error_ids}")
    if with_answer:
        refuse_cases = [r for r in results if r.refuse_ok is not None]
        source_cases = [r for r in results if r.source_hit is not None]
        if refuse_cases:
            ok = sum(1 for r in refuse_cases if r.refuse_ok)
            print(f"refuse_ok: {ok}/{len(refuse_cases)}")
        if source_cases:
            ok = sum(1 for r in source_cases if r.source_hit)
            print(f"source_hit: {ok}/{len(source_cases)}")
        if summary.citation_coverage is not None:
            print(f"citation_coverage: {_fmt(summary.citation_coverage)}")


def _print_verdict_summary(summary: VerdictSummary) -> None:
    """判定正確率／誤配率的報告輸出：claim-verdict-card/specs/rag-eval/spec.md
    要求印出判定正確率、誤配率、判定錯誤的題目 id（標示相鄰/顛倒）、誤配的
    題目 id——五項都在這裡逐行印出，缺一都算沒交付這個規格。
    """

    def _fmt(value: Optional[float]) -> str:
        return f"{value:.3f}" if value is not None else "n/a"

    print("=== Verdict Summary (--with-verdict) ===")
    print(f"scored_cases: {summary.scored_cases}")
    print(f"correct: {summary.correct}")
    print(f"verdict_accuracy: {_fmt(summary.verdict_accuracy)}")
    print(f"wrong_ids: {summary.wrong_ids}")
    print(f"  adjacent (相鄰，序上距離 1，使用者仍被告知說法有問題): {summary.adjacent_wrong_ids}")
    print(f"  reversed (顛倒，嚴重失效): {summary.reversed_wrong_ids}")
    print(f"mismatch_cases: {summary.mismatch_cases}")
    print(f"mismatches: {summary.mismatches}")
    print(f"mismatch_rate: {_fmt(summary.mismatch_rate)}")
    print(f"mismatch_ids: {summary.mismatch_ids}")
    if summary.error_ids:
        print(f"error_ids: {summary.error_ids}")


async def run_eval(
    golden: Path,
    *,
    with_answer: bool = False,
    split: str = "",
    rank_mode: str = "none",
    top_n: Optional[int] = None,
    retriever=None,
    answer_service=None,
    reranker=None,
) -> tuple[list[CaseResult], object]:
    cases = load_golden_jsonl(golden)
    if split:
        cases = [c for c in cases if c.split == split]

    if retriever is None:
        retriever = get_rag_retriever()
    if with_answer and answer_service is None:
        answer_service = get_rag_answer_service()
    if reranker is None and rank_mode != "none":
        reranker = _build_reranker(rank_mode)

    effective_top_n = top_n if top_n is not None else settings.RAG_RERANK_TOP_N

    results: list[CaseResult] = []
    for case in cases:
        results.append(
            await _eval_one(
                case,
                retriever=retriever,
                answer_service=answer_service,
                with_answer=with_answer,
                rank_mode=rank_mode,
                top_n=effective_top_n,
                reranker=reranker,
            )
        )
    summary = summarize_results(results)
    return results, summary


async def run_verdict_eval(
    golden: Path,
    *,
    split: str = "",
    claim_verification_service,
) -> tuple[list[VerdictResult], Optional[VerdictSummary]]:
    """對有 `expected_verdict` 的題目呼叫 `ClaimVerificationService.verify()`，
    量判定正確率／誤配率（claim-verdict-card/specs/rag-eval/spec.md）。

    `claim_verification_service` 沒有預設值、必須明確傳入——不像 `run_eval`
    的 `retriever`／`answer_service` 省略時會自動查全域設定，這裡刻意不做
    那層隱式 fallback：`CLAIM_VERIFICATION_ENABLED=false` 時「有沒有服務」
    是呼叫端（`main()`）必須先問清楚、且要能被單元測試決定性覆蓋的事——
    若這裡也做「省略就查全域」，測試「服務未配置」這條路徑時，結果會依
    當下 `.env` 的 `CLAIM_VERIFICATION_ENABLED` 而定，同一則測試在不同機器
    上可能一次過一次不過。傳 `None` 進來單純代表「沒有服務」，回傳
    `([], None)`，不拋例外，讓 `main()` 印出清楚的跳過訊息。

    每題最多 3 次 LLM 呼叫（主張正規化、同一性驗證、理由改寫）——這是
    `--with-verdict` 預設關閉的原因，不能讓既有 `rag_eval.py` 的日常跑法
    變慢變貴。單題呼叫失敗（逾時、例外）不會讓整批中斷：該題記一筆帶
    `error` 的 `VerdictResult`，`summarize_verdicts` 會把它排除在兩個指標
    的分母之外，比照 `_eval_one` 對檢索/答案層例外的既有處理方式。

    `verify()` 成功但回傳的 verdict 不在 `VALID_VERDICTS` 內時，一樣記一筆
    `error`，不會把值直接交給 `score_verdict()`——`score_verdict` 內部的
    `verdict_severity_distance()` 對非法值是刻意 `.index()` 拋 `ValueError`
    （見該函式 docstring：誤配是本功能唯一的嚴重失效模式，非法值不該被靜默
    分類），這裡的驗證正是那份「呼叫端保證引數合法」契約的實踐者。這個檢查
    不是防禦性程式設計的空氣——CARE-data 的 `LEGACY_PREFIX_VERDICT` 對照表
    才剛發生過漏收「正確」前綴、導致 verdict 寫成 `None` 的真實事故
    （task-8-report.md），資料清洗上游一旦再出錯，這裡就是最後一道不讓整批
    評測（連同已經算好、還沒來得及寫進 `--out` 的 hit_rate）陪葬的防線。
    """
    if claim_verification_service is None:
        return [], None

    cases = load_golden_jsonl(golden)
    if split:
        cases = [c for c in cases if c.split == split]
    verdict_cases = [c for c in cases if c.expected_verdict]

    results: list[VerdictResult] = []
    for case in verdict_cases:
        try:
            verification = await claim_verification_service.verify(case.query)
        except Exception as exc:  # noqa: BLE001 - 一題失敗不能讓整批中斷
            results.append(
                VerdictResult(
                    id=case.id,
                    expected_verdict=case.expected_verdict,
                    actual_verdict="",
                    applicable=False,
                    error=f"verify: {type(exc).__name__}: {exc}",
                )
            )
            continue

        actual_verdict = verification.verdict
        if actual_verdict not in VALID_VERDICTS:
            results.append(
                VerdictResult(
                    id=case.id,
                    expected_verdict=case.expected_verdict,
                    actual_verdict=actual_verdict,
                    applicable=False,
                    error=(
                        f"invalid verdict from service: {actual_verdict!r} "
                        f"not in {sorted(VALID_VERDICTS)}"
                    ),
                )
            )
            continue

        results.append(score_verdict(case, actual_verdict))

    return results, summarize_verdicts(results)


async def run_compare_rerank(
    golden: Path,
    *,
    split: str = "",
    top_n: Optional[int] = None,
    retriever=None,
) -> dict:
    """同一批 wide-retrieve，分別以 vector / cohere top-n 計分。"""
    cases = load_golden_jsonl(golden)
    if split:
        cases = [c for c in cases if c.split == split]
    if retriever is None:
        retriever = get_rag_retriever()

    effective_top_n = top_n if top_n is not None else settings.RAG_RERANK_TOP_N
    vector_rr = VectorScoreReranker()
    cohere_rr = _build_reranker("cohere")

    vector_results: list[CaseResult] = []
    cohere_results: list[CaseResult] = []

    for case in cases:
        needs = case.route == "kb" and case.has_retrieval_expectations
        if not needs:
            skipped = score_case_retrieval(case, [])
            skipped.rank_mode = "vector"
            vector_results.append(skipped)
            skipped2 = score_case_retrieval(case, [])
            skipped2.rank_mode = "cohere"
            cohere_results.append(skipped2)
            continue
        try:
            wide = await retriever.ainvoke(case.query)
            # 兩個分支都經過 _maybe_rank（rerank 全排 → 文章層級去重 → 截
            # top_n），鏡射 production 行為——production 無論用哪個
            # reranker（Cohere 或降級用的 VectorScoreReranker）都會套用
            # 同一套去重，這裡的 vector／cohere 分支分別代表「如果那天是
            # 這個 reranker 在跑」。
            v_docs = await _maybe_rank(
                wide,
                query=case.query,
                rank_mode="vector",
                top_n=effective_top_n,
                reranker=vector_rr,
            )
            c_docs = await _maybe_rank(
                wide,
                query=case.query,
                rank_mode="cohere",
                top_n=effective_top_n,
                reranker=cohere_rr,
            )
        except Exception as exc:  # noqa: BLE001
            err = CaseResult(
                id=case.id,
                query=case.query,
                route=case.route,
                skipped=False,
                retrieval_hit=False,
                retrieved_urls=[],
                error=f"retrieve: {type(exc).__name__}: {exc}",
            )
            err.rank_mode = "vector"
            vector_results.append(err)
            err2 = CaseResult(
                id=case.id,
                query=case.query,
                route=case.route,
                skipped=False,
                retrieval_hit=False,
                retrieved_urls=[],
                error=f"retrieve: {type(exc).__name__}: {exc}",
                rank_mode="cohere",
            )
            cohere_results.append(err2)
            continue

        vr = score_case_retrieval(case, v_docs)
        vr.rank_mode = "vector"
        vector_results.append(vr)
        cr = score_case_retrieval(case, c_docs)
        cr.rank_mode = "cohere"
        cohere_results.append(cr)

    return {
        "vector": {
            "summary": summarize_results(vector_results),
            "results": vector_results,
        },
        "cohere": {
            "summary": summarize_results(cohere_results),
            "results": cohere_results,
        },
        "top_n": effective_top_n,
    }


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    top_n = args.top_n

    try:
        golden = _resolve_golden_path(args.golden)
        out_path = _resolve_out_path(args.out) if args.out is not None else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.compare_rerank:
        compare = asyncio.run(
            run_compare_rerank(
                golden,
                split=args.split.strip(),
                top_n=top_n,
            )
        )
        v_sum = compare["vector"]["summary"]
        c_sum = compare["cohere"]["summary"]
        _print_summary(
            f"vector top-{compare['top_n']}",
            golden,
            v_sum,
            compare["vector"]["results"],
            with_answer=False,
        )
        print()
        _print_summary(
            f"cohere top-{compare['top_n']}",
            golden,
            c_sum,
            compare["cohere"]["results"],
            with_answer=False,
        )
        print()
        print("=== Delta (cohere - vector) ===")
        if v_sum.hit_rate is not None and c_sum.hit_rate is not None:
            delta = c_sum.hit_rate - v_sum.hit_rate
            print(f"hit_rate_delta: {delta:+.3f}")
            only_vector = sorted(set(c_sum.miss_ids) - set(v_sum.miss_ids))
            only_cohere_fixed = sorted(set(v_sum.miss_ids) - set(c_sum.miss_ids))
            print(f"fixed_by_cohere: {only_cohere_fixed}")
            print(f"regressed_by_cohere: {only_vector}")
        else:
            print("hit_rate_delta: n/a")
        if v_sum.mean_ndcg_at_5 is not None and c_sum.mean_ndcg_at_5 is not None:
            print(
                "ndcg@5_delta: "
                f"{c_sum.mean_ndcg_at_5 - v_sum.mean_ndcg_at_5:+.3f}"
            )

        if out_path is not None:
            payload = {
                "top_n": compare["top_n"],
                "vector": {
                    "summary": v_sum.to_dict(),
                    "results": [r.to_dict() for r in compare["vector"]["results"]],
                },
                "cohere": {
                    "summary": c_sum.to_dict(),
                    "results": [r.to_dict() for r in compare["cohere"]["results"]],
                },
            }
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"wrote: {out_path}")

        if args.fail_under is not None:
            rate = c_sum.hit_rate
            if rate is None or rate < args.fail_under:
                return 1
        return 0

    results, summary = asyncio.run(
        run_eval(
            golden,
            with_answer=args.with_answer,
            split=args.split.strip(),
            rank_mode=args.rank_mode,
            top_n=top_n,
        )
    )

    _print_summary(
        args.rank_mode,
        golden,
        summary,
        results,
        with_answer=args.with_answer,
    )

    verdict_results: list[VerdictResult] = []
    verdict_summary: Optional[VerdictSummary] = None
    if args.with_verdict:
        claim_verification_service = get_claim_verification_service()
        if claim_verification_service is None:
            print(
                "\nwith-verdict: CLAIM_VERIFICATION_ENABLED=false 或 "
                "ClaimVerificationService 未接線，略過判定正確率／誤配率計分"
            )
        else:
            print()
            verdict_results, verdict_summary = asyncio.run(
                run_verdict_eval(
                    golden,
                    split=args.split.strip(),
                    claim_verification_service=claim_verification_service,
                )
            )
            _print_verdict_summary(verdict_summary)

    if out_path is not None:
        payload = {
            "summary": summary.to_dict(),
            "rank_mode": args.rank_mode,
            "results": [r.to_dict() for r in results],
        }
        if verdict_summary is not None:
            payload["verdict"] = {
                "summary": verdict_summary.to_dict(),
                "results": [r.to_dict() for r in verdict_results],
            }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"wrote: {out_path}")

    if args.fail_under is not None:
        if summary.hit_rate is None:
            print("fail-under: no scored cases", file=sys.stderr)
            return 1
        if summary.hit_rate < args.fail_under:
            print(
                f"fail-under: hit_rate {summary.hit_rate:.3f} < {args.fail_under}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
