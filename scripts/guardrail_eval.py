#!/usr/bin/env python3
"""評測 guardrail 分類器：現行的 Gemini 呼叫，或之後的本地模型。

為什麼指標不只看 accuracy：兩種錯誤的代價完全不對稱。

  - **漏判**（正例判成負例）→ RAG 工具不掛給 agent → 使用者問醫療問題卻
    得到空泛回答。**這是真傷害。**
  - **誤判**（負例判成正例）→ 白掛一個工具，agent 未必會用；就算用了也只是
    多花一次檢索。**這是浪費，不是傷害。**

所以報告把兩者分開列，並以「漏判率」為主要決策指標；本地模型要取代 Gemini
的門檻是「漏判率不高於現行值」，而不是「F1 比較高」。

用法（專案根目錄，需先 source .venv）：
  python scripts/guardrail_eval.py --classifier llm --split holdout --limit 200
  python scripts/guardrail_eval.py --classifier llm --out /tmp/guardrail-llm.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from app.core.config import settings  # noqa: E402
from app.services.gemini import GeminiService  # noqa: E402
from app.services.guardrail import (  # noqa: E402
    CascadeGuardrailService,
    GuardrailService,
    LocalGuardrailClassifier,
)

DEFAULT_DATASET = _PROJECT_ROOT / "evals" / "guardrail" / "dataset.jsonl"


def _load(path: Path, split: str, limit: Optional[int], seed: int) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if split:
        rows = [r for r in rows if r.get("split") == split]
    if limit and limit < len(rows):
        # 分層抽樣：直接截斷會讓 bucket 分佈失衡，而各 bucket 的難度差很多
        # （adjacent_but_no 與 smalltalk 不是同一個等級的題目）。
        by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_bucket[row["bucket"]].append(row)
        rng = random.Random(seed)
        per = max(1, limit // len(by_bucket))
        sampled: list[dict[str, Any]] = []
        for bucket_rows in by_bucket.values():
            rng.shuffle(bucket_rows)
            sampled.extend(bucket_rows[:per])
        rng.shuffle(sampled)
        rows = sampled[:limit]
    return rows


def _build_llm_classifier() -> Callable[[str], Any]:
    """現行線上的判斷路徑，逐字沿用 dependencies.py 的組裝方式。"""
    gemini = GeminiService(
        api_key=settings.GEMINI_API_KEY,
        model_name=settings.MODEL_NAME,
    )
    service = GuardrailService(
        async_text_to_bool=gemini.invoke_boolean_structured_output,
    )
    return service.allow_rag_tool


def _report(results: list[dict[str, Any]]) -> dict[str, Any]:
    tp = sum(1 for r in results if r["label"] == 1 and r["pred"] == 1)
    fn = sum(1 for r in results if r["label"] == 1 and r["pred"] == 0)
    tn = sum(1 for r in results if r["label"] == 0 and r["pred"] == 0)
    fp = sum(1 for r in results if r["label"] == 0 and r["pred"] == 1)

    def _f1(p_tp: int, p_fp: int, p_fn: int) -> float:
        denom = 2 * p_tp + p_fp + p_fn
        return (2 * p_tp / denom) if denom else 0.0

    positives = tp + fn
    negatives = tn + fp
    latencies = sorted(r["ms"] for r in results)
    return {
        "n": len(results),
        "accuracy": (tp + tn) / len(results) if results else 0.0,
        "macro_f1": (_f1(tp, fp, fn) + _f1(tn, fn, fp)) / 2,
        "miss_rate": fn / positives if positives else None,
        "false_alarm_rate": fp / negatives if negatives else None,
        "tp": tp, "fn": fn, "tn": tn, "fp": fp,
        "ms_p50": latencies[len(latencies) // 2] if latencies else None,
        "ms_p90": latencies[int(len(latencies) * 0.9)] if latencies else None,
        "ms_max": latencies[-1] if latencies else None,
    }


def _print(name: str, summary: dict[str, Any], results: list[dict[str, Any]]) -> None:
    print(f"\n=== guardrail eval · {name} · n={summary['n']} ===")
    print(f"accuracy   {summary['accuracy']:.3f}    macro-F1 {summary['macro_f1']:.3f}")
    print(
        f"漏判率     {summary['miss_rate']:.3f}  ({summary['fn']} 則醫療問題被判成不相關) ← 真傷害"
    )
    print(
        f"誤判率     {summary['false_alarm_rate']:.3f}  ({summary['fp']} 則無關訊息被判成相關) ← 只是浪費"
    )
    print(f"延遲       p50={summary['ms_p50']}ms  p90={summary['ms_p90']}ms  max={summary['ms_max']}ms")

    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        by_bucket[row["bucket"]].append(row)
    print("\n各 bucket 錯誤數：")
    for bucket in sorted(by_bucket, key=lambda b: -sum(1 for r in by_bucket[b] if r["pred"] != r["label"])):
        rows = by_bucket[bucket]
        wrong = [r for r in rows if r["pred"] != r["label"]]
        if not wrong:
            continue
        print(f"  {bucket:<20} {len(wrong)}/{len(rows)}")
        for row in wrong[:3]:
            print(f"      「{row['text'][:44]}」")


async def _run(args: argparse.Namespace) -> int:
    rows = _load(args.dataset, args.split, args.limit, args.seed)
    if not rows:
        print("資料集是空的——先跑 scripts/build_guardrail_dataset.py", file=sys.stderr)
        return 2

    escalations = 0

    if args.classifier == "llm":
        classify = _build_llm_classifier()
    elif args.classifier == "local":
        # 純本地：不看門檻、一律以 0.5 判定。這條**不是**要上線的設定，
        # 是用來量「若完全取代 LLM 會如何」的對照組。
        local = LocalGuardrailClassifier.load()

        async def classify(text: str) -> bool:  # type: ignore[misc]
            return local.probability(text) >= 0.5
    else:  # cascade
        local = LocalGuardrailClassifier.load()
        llm_service = GuardrailService(
            async_text_to_bool=GeminiService(
                api_key=settings.GEMINI_API_KEY, model_name=settings.MODEL_NAME
            ).invoke_boolean_structured_output
        )
        cascade = CascadeGuardrailService(local=local, fallback=llm_service)

        async def classify(text: str) -> bool:  # type: ignore[misc]
            nonlocal escalations
            probability = local.probability(text)
            if local.low <= probability < local.high:
                escalations += 1
            return await cascade.allow_rag_tool(text)

    sem = asyncio.Semaphore(args.concurrency)

    async def one(row: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            t0 = time.perf_counter()
            try:
                pred = bool(await classify(row["text"]))
            except Exception as exc:
                print(f"  ! 分類失敗：{exc!r}", file=sys.stderr)
                # fail-open 與線上一致：判不出來時放行
                pred = True
            ms = int((time.perf_counter() - t0) * 1000)
        return {**row, "pred": int(pred), "ms": ms}

    print(f"評測 {len(rows)} 筆（{args.classifier}，concurrency={args.concurrency}）…")
    results = await asyncio.gather(*(one(r) for r in rows))
    summary = _report(results)
    if args.classifier == "cascade":
        summary["escalated"] = escalations
        summary["local_share"] = 1 - escalations / len(results)
    _print(args.classifier, summary, results)
    if args.classifier == "cascade":
        print(
            f"\n本地解掉 {summary['local_share']:.1%}"
            f"（{len(results) - escalations}/{len(results)}），"
            f"升級給 LLM {escalations} 次"
        )

    if args.out:
        args.out.write_text(
            json.dumps({"classifier": args.classifier, "summary": summary, "results": results},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nwrote {args.out}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--classifier", default="cascade", choices=["llm", "local", "cascade"]
    )
    parser.add_argument("--split", default="holdout")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--seed", type=int, default=20260909)
    parser.add_argument("--out", type=Path, default=None)
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
