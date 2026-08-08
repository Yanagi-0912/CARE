#!/usr/bin/env python3
"""批次評測 RAG 檢索（可選答案層／精排 A/B）。

用法（專案根目錄）：
  python scripts/rag_eval.py
  python scripts/rag_eval.py --with-answer --out /tmp/rag-report.json
  python scripts/rag_eval.py --rank-mode vector --top-n 5
  python scripts/rag_eval.py --compare-rerank --out /tmp/rag-compare.json
  python scripts/rag_eval.py --fail-under 0.6
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
from app.dependencies import get_rag_answer_service, get_rag_retriever
from app.services.rag.cohere_reranker import CohereReranker, VectorScoreReranker
from app.services.rag.eval_scoring import (
    CaseResult,
    EvalCase,
    load_golden_jsonl,
    score_case_retrieval,
    is_refuse_ok,
    is_source_hit,
    summarize_results,
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
    if rank_mode == "none" or reranker is None:
        return docs
    return await reranker.rerank(query, docs, top_n=top_n)


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
            v_docs = await vector_rr.rerank(case.query, wide, top_n=effective_top_n)
            c_docs = await cohere_rr.rerank(case.query, wide, top_n=effective_top_n)
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

    if out_path is not None:
        payload = {
            "summary": summary.to_dict(),
            "rank_mode": args.rank_mode,
            "results": [r.to_dict() for r in results],
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
