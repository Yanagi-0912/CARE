#!/usr/bin/env python3
"""批次評測 RAG 檢索（可選答案層）。

用法（專案根目錄）：
  python scripts/rag_eval.py
  python scripts/rag_eval.py --with-answer --out /tmp/rag-report.json
  python scripts/rag_eval.py --fail-under 0.6
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from app.dependencies import get_rag_answer_service, get_rag_retriever
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
    return parser.parse_args(argv)


async def _eval_one(
    case: EvalCase,
    *,
    retriever,
    answer_service,
    with_answer: bool,
) -> CaseResult:
    needs_retrieval_score = case.route == "kb" and bool(case.expected_url_substrings)
    if not needs_retrieval_score and not with_answer:
        return score_case_retrieval(case, [])

    try:
        docs = await retriever.ainvoke(case.query)
    except Exception as exc:  # noqa: BLE001 - 評測要收斂錯誤到報告
        return CaseResult(
            id=case.id,
            query=case.query,
            route=case.route,
            skipped=not needs_retrieval_score,
            retrieval_hit=False if needs_retrieval_score else None,
            retrieved_urls=[],
            error=f"retrieve: {type(exc).__name__}: {exc}",
        )

    result = score_case_retrieval(case, docs)

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


async def run_eval(
    golden: Path,
    *,
    with_answer: bool = False,
    split: str = "",
    retriever=None,
    answer_service=None,
) -> tuple[list[CaseResult], object]:
    cases = load_golden_jsonl(golden)
    if split:
        cases = [c for c in cases if c.split == split]

    if retriever is None:
        retriever = get_rag_retriever()
    if with_answer and answer_service is None:
        answer_service = get_rag_answer_service()

    results: list[CaseResult] = []
    for case in cases:
        results.append(
            await _eval_one(
                case,
                retriever=retriever,
                answer_service=answer_service,
                with_answer=with_answer,
            )
        )
    summary = summarize_results(results)
    return results, summary


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    results, summary = asyncio.run(
        run_eval(
            args.golden,
            with_answer=args.with_answer,
            split=args.split.strip(),
        )
    )

    print("=== RAG Eval Summary ===")
    print(f"golden: {args.golden}")
    print(f"total_cases: {summary.total_cases}")
    print(f"scored_cases: {summary.scored_cases}")
    print(f"hits: {summary.hits}")
    rate = summary.hit_rate
    print(f"hit_rate: {rate if rate is not None else 'n/a'}")
    print(f"miss_ids: {summary.miss_ids}")
    print(f"skipped_ids: {summary.skipped_ids}")
    if summary.error_ids:
        print(f"error_ids: {summary.error_ids}")

    if args.with_answer:
        refuse_cases = [r for r in results if r.refuse_ok is not None]
        source_cases = [r for r in results if r.source_hit is not None]
        if refuse_cases:
            ok = sum(1 for r in refuse_cases if r.refuse_ok)
            print(f"refuse_ok: {ok}/{len(refuse_cases)}")
        if source_cases:
            ok = sum(1 for r in source_cases if r.source_hit)
            print(f"source_hit: {ok}/{len(source_cases)}")

    if args.out:
        payload = {
            "summary": summary.to_dict(),
            "results": [r.to_dict() for r in results],
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"wrote: {args.out}")

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
