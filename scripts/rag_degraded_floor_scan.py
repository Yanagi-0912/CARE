#!/usr/bin/env python3
"""量「雙重失效」那條路徑上的分數分佈，供 RAG_DEGRADED_MIN_SCORE 校準。

要校準的是什麼：`RagAnswerService._filter_by_degraded_score` 在 CRAG grader
失效時，用精排分數當最後一道相關性把關。Cohere 也失效（逾時／429／無 key）
時，那個分數會退回**融合分數**——而 `RAG_DEGRADED_MIN_SCORE=0.3` 是照 Cohere
`relevance_score` 的分佈訂的，換到融合分數的尺度上意義完全不同。

本腳本複製那條路徑（HybridRetriever → VectorScoreReranker → top_n），對每一
篇候選同時記錄三個數字，再逐一畫出門檻的取捨曲線：

  - `fusion`      融合分數（RRF 或 convex，看 --fusion-mode）
  - `vector`      該篇的原始 cosine 相似度（metadata["vector_score"]）
  - `relevant`    是否命中 golden set 的期望 substring（與 hit_rate 同判準）

用法（專案根目錄，需先 source .venv）：
  python scripts/rag_degraded_floor_scan.py
  python scripts/rag_degraded_floor_scan.py --fusion-mode rrf
  python scripts/rag_degraded_floor_scan.py --alpha 0.6 --out /tmp/floor.json

**不打 Cohere**：這條路徑的前提就是 Cohere 已經失效，所以量的是
VectorScoreReranker 的輸出。只花 embedding 與 Atlas 的錢。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.rag_eval import (  # noqa: E402
    DEFAULT_GOLDEN,
    _resolve_golden_path,
    _resolve_out_path,
)

from app.core.config import settings  # noqa: E402
from app.dependencies import get_rag_retriever  # noqa: E402
from app.services.rag.cohere_reranker import VectorScoreReranker  # noqa: E402
from app.services.rag.eval_scoring import doc_relevances, load_golden_jsonl  # noqa: E402
from app.services.rag.rank_fusion import FUSION_MODE_CONVEX, FUSION_MODES  # noqa: E402
from app.services.rag.retriever import HybridRetriever  # noqa: E402

THRESHOLD_GRID: tuple[float, ...] = (
    0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9,
)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--split", default="", help="空字串＝全部（校準要盡量多題）")
    parser.add_argument("--fusion-mode", default=FUSION_MODE_CONVEX, choices=list(FUSION_MODES))
    parser.add_argument("--alpha", type=float, default=0.6)
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args(argv)


def _describe(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "min": ordered[0],
        "p25": statistics.quantiles(ordered, n=4)[0] if len(ordered) > 3 else ordered[0],
        "median": statistics.median(ordered),
        "p75": statistics.quantiles(ordered, n=4)[2] if len(ordered) > 3 else ordered[-1],
        "max": ordered[-1],
    }


def _print_describe(label: str, stats: dict[str, Any]) -> None:
    if not stats.get("n"):
        print(f"{label:<28} (無資料)")
        return
    print(
        f"{label:<28}n={stats['n']:<4}min={stats['min']:.3f}  p25={stats['p25']:.3f}  "
        f"median={stats['median']:.3f}  p75={stats['p75']:.3f}  max={stats['max']:.3f}"
    )


def _threshold_table(field: str, per_case: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """逐個門檻算取捨。

    三個欄位的意思：
      - `cases_losing_gold`：本來 top-n 裡有相關文件，套門檻後全被刷掉。
        **這是這張網的傷害面**——該從知識庫答的題目被推去網搜。
      - `cases_emptied`：套門檻後 top-n 全空（含本來就沒有相關文件的題目）。
      - `junk_dropped`：被刷掉的不相關文件佔全部不相關文件的比例，是效益面。
    """
    rows = []
    for threshold in THRESHOLD_GRID:
        losing = 0
        emptied = 0
        junk_total = 0
        junk_dropped = 0
        for case in per_case:
            kept = [d for d in case["docs"] if d[field] >= threshold]
            had_gold = any(d["relevant"] for d in case["docs"])
            keeps_gold = any(d["relevant"] for d in kept)
            if had_gold and not keeps_gold:
                losing += 1
            if not kept:
                emptied += 1
            for doc in case["docs"]:
                if not doc["relevant"]:
                    junk_total += 1
                    if doc[field] < threshold:
                        junk_dropped += 1
        rows.append(
            {
                "threshold": threshold,
                "cases_losing_gold": losing,
                "cases_emptied": emptied,
                "junk_dropped": junk_dropped,
                "junk_total": junk_total,
                "junk_dropped_rate": (junk_dropped / junk_total) if junk_total else None,
            }
        )
    return rows


def _print_threshold_table(label: str, rows: list[dict[str, Any]], total_cases: int) -> None:
    print(f"\n--- 門檻取捨（依 {label} 分數）· 共 {total_cases} 題 ---")
    print(f"{'threshold':>10}{'丟掉相關的題數':>16}{'整題清空':>12}{'刷掉的雜訊':>14}")
    for row in rows:
        rate = row["junk_dropped_rate"]
        junk = f"{row['junk_dropped']}/{row['junk_total']}"
        if rate is not None:
            junk += f" ({rate:.0%})"
        print(
            f"{row['threshold']:>10.2f}{row['cases_losing_gold']:>16}"
            f"{row['cases_emptied']:>12}{junk:>14}"
        )


async def _run(args: argparse.Namespace) -> int:
    golden = _resolve_golden_path(args.golden)
    if not golden.exists():
        print(f"golden set not found: {golden}", file=sys.stderr)
        return 2

    base = get_rag_retriever()
    if not isinstance(base, HybridRetriever):
        print(
            "目前的 retriever 不是 HybridRetriever——沒有融合分數可量。\n"
            "請確認 .env 的 RAG_HYBRID_ENABLED=true 且 MONGODB_TEXT_INDEX 已設定。",
            file=sys.stderr,
        )
        return 2

    retriever = HybridRetriever(
        vector_retriever=base.vector_retriever,
        text_retriever=base.text_retriever,
        rrf_k=base.rrf_k,
        limit=base.limit,
        fusion_mode=args.fusion_mode,
        alpha=args.alpha,
    )
    reranker = VectorScoreReranker()
    top_n = args.top_n if args.top_n is not None else settings.RAG_RERANK_TOP_N

    cases = [c for c in load_golden_jsonl(golden) if c.has_retrieval_expectations]
    if args.split:
        cases = [c for c in cases if c.split == args.split]

    per_case: list[dict[str, Any]] = []
    for case in cases:
        docs = await retriever.ainvoke(case.query)
        ranked = await reranker.rerank(case.query, docs, top_n=top_n)
        relevances = doc_relevances(case, ranked)
        per_case.append(
            {
                "id": case.id,
                "split": case.split,
                "docs": [
                    {
                        "fusion": float(doc.metadata.get("score") or 0.0),
                        "vector": float(doc.metadata.get("vector_score") or 0.0),
                        "relevant": bool(rel),
                        "retrievers": doc.metadata.get("retrievers"),
                    }
                    for doc, rel in zip(ranked, relevances)
                ],
            }
        )

    label = args.fusion_mode
    if args.fusion_mode == FUSION_MODE_CONVEX:
        label += f" α={args.alpha:g}"
    print(f"=== 降級路徑分數分佈（fusion={label}, top_n={top_n}）===")
    print(f"golden: {golden}  題數: {len(per_case)}")

    flat = [doc for case in per_case for doc in case["docs"]]
    relevant = [d for d in flat if d["relevant"]]
    irrelevant = [d for d in flat if not d["relevant"]]
    print()
    _print_describe("融合分數 · 相關", _describe([d["fusion"] for d in relevant]))
    _print_describe("融合分數 · 不相關", _describe([d["fusion"] for d in irrelevant]))
    _print_describe("原始 cosine · 相關", _describe([d["vector"] for d in relevant]))
    _print_describe("原始 cosine · 不相關", _describe([d["vector"] for d in irrelevant]))

    fusion_rows = _threshold_table("fusion", per_case)
    vector_rows = _threshold_table("vector", per_case)
    _print_threshold_table("融合", fusion_rows, len(per_case))
    _print_threshold_table("原始 cosine", vector_rows, len(per_case))

    if args.out:
        out = _resolve_out_path(args.out)
        out.write_text(
            json.dumps(
                {
                    "fusion_mode": args.fusion_mode,
                    "alpha": args.alpha,
                    "top_n": top_n,
                    "cases": per_case,
                    "fusion_thresholds": fusion_rows,
                    "vector_thresholds": vector_rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {out}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
