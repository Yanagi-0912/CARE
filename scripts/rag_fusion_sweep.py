#!/usr/bin/env python3
"""在 golden set 上掃融合權重：RRF 基準 vs 凸組合的各個 alpha。

為什麼需要這支：`RAG_RRF_K=60` 出自 RRF 原始論文（Cormack et al., SIGIR
2009）在 TREC 上用的值，本專案從未校準過。Bruch et al.（TOIS 42(1), 2023；
arXiv:2210.11934）量到凸組合勝過 RRF，且其權重「只需少量標註查詢」即可調
出來——golden.jsonl 的 44 題 train 剛好落在這個量級。

用法（專案根目錄，需先 source .venv）：
  python scripts/rag_fusion_sweep.py
  python scripts/rag_fusion_sweep.py --rank-mode vector cohere --verify-holdout
  python scripts/rag_fusion_sweep.py --alphas 0.3 0.4 0.5 --out /tmp/fusion-sweep.json

三件必須先知道的事：

1. **各腿只會被實際查詢一次。** 每個 alpha 都重跑檢索的話，55 題 × 11 個
   alpha 會打 605 次 embedding API；本腳本用 `_CachedLeg` 把兩條腿的結果
   按 query 快取起來，融合純粹在記憶體裡重算。所以掃 3 個或 30 個 alpha 的
   檢索成本一樣。
2. **`--rank-mode cohere` 不在這個快取的保護範圍內**：精排是 per-alpha 的
   外部呼叫（題數 × alpha 數）。這是刻意的——Cohere 看到的候選集本來就會
   隨 alpha 改變，快取它就等於量錯東西。
3. **一定要看 `hits/scored` 的絕對數字，不要只看 hit_rate。** n=44 時一題
   就是 2.3 個百分點；`RAG_TEXT_TITLE_BOOST` 的掃描結果之所以可信，是因為
   它是平滑單峰而不是單點突起。單點跳高多半是雜訊。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from langchain_core.documents import Document

from scripts.rag_eval import (  # noqa: E402  （必須在 sys.path 補好之後）
    DEFAULT_GOLDEN,
    _build_reranker,
    _resolve_golden_path,
    _resolve_out_path,
    run_eval,
)

from app.core.config import settings  # noqa: E402
from app.dependencies import get_rag_retriever  # noqa: E402
from app.services.rag.rank_fusion import FUSION_MODE_CONVEX, FUSION_MODE_RRF  # noqa: E402
from app.services.rag.retriever import HybridRetriever  # noqa: E402

DEFAULT_ALPHAS: tuple[float, ...] = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


class _CachedLeg:
    """把單一條腿的檢索結果按 query 快取起來。

    只實作 `ainvoke` 與 `warmup`——`HybridRetriever` 只用到這兩個方法。
    刻意不做 LRU 淘汰：一次掃描最多 55 個 query × 40 篇，記憶體不是問題，
    而淘汰會讓「同一個 query 在不同 alpha 拿到不同結果」變成可能，那會讓
    整份掃描結果不可比。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._cache: dict[str, list[Document]] = {}
        self.calls = 0

    async def warmup(self) -> None:
        await self._inner.warmup()

    async def ainvoke(self, query: str) -> list[Document]:
        if query not in self._cache:
            self.calls += 1
            self._cache[query] = await self._inner.ainvoke(query)
        # 回傳複本：融合會覆寫 metadata["score"]，共用同一批 Document 物件
        # 會讓第二個 alpha 讀到第一個 alpha 寫進去的融合分數。
        return [
            Document(page_content=d.page_content, metadata=dict(d.metadata))
            for d in self._cache[query]
        ]


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument(
        "--split",
        default="train",
        help="掃描用的切分（預設 train；空字串＝全部）",
    )
    parser.add_argument(
        "--holdout-split",
        default="holdout",
        help="--verify-holdout 時用哪個切分驗證",
    )
    parser.add_argument(
        "--verify-holdout",
        action="store_true",
        help="掃完後，用最佳 alpha 與 RRF 基準在 holdout 上各跑一次",
    )
    parser.add_argument(
        "--rank-mode",
        nargs="+",
        default=["vector"],
        choices=["none", "vector", "cohere"],
        help=(
            "要量的精排切面，可給多個。至少看 vector（降級路徑）與 cohere"
            "（正常路徑）兩個——RAG_TEXT_TITLE_BOOST 的增益就是只存在於前者。"
        ),
    )
    parser.add_argument("--alphas", nargs="+", type=float, default=list(DEFAULT_ALPHAS))
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--no-baseline", action="store_true", help="不跑 RRF 基準")
    parser.add_argument("--out", type=Path, default=None, help="把完整結果寫成 JSON")
    return parser.parse_args(argv)


def _make_variant(base: HybridRetriever, legs: tuple[_CachedLeg, _CachedLeg], **kwargs) -> HybridRetriever:
    vector_leg, text_leg = legs
    return HybridRetriever(
        vector_retriever=vector_leg,
        text_retriever=text_leg,
        rrf_k=base.rrf_k,
        limit=base.limit,
        **kwargs,
    )


def _row(summary: Any) -> dict[str, Any]:
    return {
        "scored_cases": summary.scored_cases,
        "hits": summary.hits,
        "hit_rate": summary.hit_rate,
        "mean_mrr": summary.mean_mrr,
        "mean_ndcg_at_5": summary.mean_ndcg_at_5,
        "miss_ids": summary.miss_ids,
    }


def _fmt(value: Optional[float]) -> str:
    return f"{value:.3f}" if value is not None else "n/a"


def _print_table(rank_mode: str, rows: list[dict[str, Any]]) -> None:
    print(f"\n=== fusion sweep · rank_mode={rank_mode} ===")
    print(f"{'config':<16}{'hits':>10}{'hit_rate':>10}{'MRR':>8}{'nDCG@5':>9}")
    for row in rows:
        hits = f"{row['hits']}/{row['scored_cases']}"
        print(
            f"{row['config']:<16}{hits:>10}{_fmt(row['hit_rate']):>10}"
            f"{_fmt(row['mean_mrr']):>8}{_fmt(row['mean_ndcg_at_5']):>9}"
        )


def _best(rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """先比 hit_rate，同分再比 MRR。None 值一律排到最後。"""
    convex_rows = [r for r in rows if r["config"] != "rrf"]
    if not convex_rows:
        return None
    return max(
        convex_rows,
        key=lambda r: (
            r["hit_rate"] if r["hit_rate"] is not None else -1.0,
            r["mean_mrr"] if r["mean_mrr"] is not None else -1.0,
        ),
    )


async def _sweep_one_mode(
    *,
    base: HybridRetriever,
    legs: tuple[_CachedLeg, _CachedLeg],
    golden: Path,
    split: str,
    rank_mode: str,
    alphas: Sequence[float],
    top_n: Optional[int],
    with_baseline: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reranker = _build_reranker(rank_mode)

    if with_baseline:
        _, summary = await run_eval(
            golden,
            split=split,
            rank_mode=rank_mode,
            top_n=top_n,
            retriever=_make_variant(base, legs, fusion_mode=FUSION_MODE_RRF),
            reranker=reranker,
        )
        rows.append({"config": "rrf", "alpha": None, **_row(summary)})

    for alpha in alphas:
        _, summary = await run_eval(
            golden,
            split=split,
            rank_mode=rank_mode,
            top_n=top_n,
            retriever=_make_variant(
                base, legs, fusion_mode=FUSION_MODE_CONVEX, alpha=alpha
            ),
            reranker=reranker,
        )
        rows.append({"config": f"convex α={alpha:g}", "alpha": alpha, **_row(summary)})

    return rows


async def _run(args: argparse.Namespace) -> int:
    golden = _resolve_golden_path(args.golden)
    if not golden.exists():
        print(f"golden set not found: {golden}", file=sys.stderr)
        return 2

    base = get_rag_retriever()
    if not isinstance(base, HybridRetriever):
        print(
            "目前的 retriever 不是 HybridRetriever——融合權重無從掃起。\n"
            "請確認 .env 的 RAG_HYBRID_ENABLED=true 且 MONGODB_TEXT_INDEX 已設定。",
            file=sys.stderr,
        )
        return 2

    legs = (_CachedLeg(base.vector_retriever), _CachedLeg(base.text_retriever))
    report: dict[str, Any] = {
        "golden": str(golden),
        "split": args.split,
        "alphas": args.alphas,
        "rrf_k": base.rrf_k,
        "limit": base.limit,
        "title_boost": settings.RAG_TEXT_TITLE_BOOST,
        "modes": {},
    }

    for rank_mode in args.rank_mode:
        rows = await _sweep_one_mode(
            base=base,
            legs=legs,
            golden=golden,
            split=args.split,
            rank_mode=rank_mode,
            alphas=args.alphas,
            top_n=args.top_n,
            with_baseline=not args.no_baseline,
        )
        _print_table(rank_mode, rows)
        report["modes"][rank_mode] = {"sweep": rows}

        best = _best(rows)
        if best is None:
            continue
        print(f"best convex: {best['config']} (hit_rate={_fmt(best['hit_rate'])})")

        if args.verify_holdout:
            holdout_rows = await _sweep_one_mode(
                base=base,
                legs=legs,
                golden=golden,
                split=args.holdout_split,
                rank_mode=rank_mode,
                alphas=[best["alpha"]],
                top_n=args.top_n,
                with_baseline=True,
            )
            _print_table(f"{rank_mode} · holdout", holdout_rows)
            report["modes"][rank_mode]["holdout"] = holdout_rows

    print(
        "\n提醒：n 很小，一題就是好幾個百分點。要採信某個 alpha，看的是"
        "「平滑單峰 + holdout 同向」，不是單點最高分。"
    )
    print(f"各腿實際查詢次數：vector={legs[0].calls} text={legs[1].calls}（已快取）")

    if args.out:
        out = _resolve_out_path(args.out)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
