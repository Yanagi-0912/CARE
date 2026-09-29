"""查詢改寫的同音錯字評測（題目：evals/query_rewrite/typo_cases.jsonl）。

用法：
    python scripts/query_rewrite_typo_eval.py [--repeats 3] [--model gemini-3.8-flash]

每題照正式環境的設定呼叫一次改寫（`--model` 預設與 CARE-infra values.yaml 的
MODEL_NAME 相同、thinking 用 REWRITE_THINKING_LEVEL），重複 `--repeats` 次——
Gemini 3 的溫度固定 1.0，同一題每次結果會變。每一次呼叫都是一次付費的 Gemini 請求。

判定：
- typo 題：`accept_typo_fix` 放行，且更正後的原句含 `expect`
- clean 題：模型沒有提出任何更正（typo_to 為空），且 kb_query／zh_terms 不含 `forbid`
  的詞（規則 1：不替使用者補病名）

另外分開記「模型提了、被同音檢查擋下」的次數：那是防線有作用，但也代表模型在該題
越界過。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "evals" / "query_rewrite" / "typo_cases.jsonl"
REPORTS = ROOT / "evals" / "query_rewrite" / "reports"


async def _run(args: argparse.Namespace) -> int:
    from app.core.config import settings
    from app.services.gemini import GeminiService
    from app.services.rag import query_rewriter as qr

    gemini = GeminiService(
        api_key=settings.GEMINI_API_KEY,
        model_name=args.model,
        thinking_level=qr.REWRITE_THINKING_LEVEL,
    )
    real = qr.GeminiQueryRewriter(gemini_service=gemini)
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line]
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(case: dict, attempt: int) -> dict:
        raw_holder: dict = {}

        async def invoke(prompt: str) -> dict:
            raw = await real._call(prompt)
            raw_holder.update(raw if isinstance(raw, dict) else {})
            return raw

        async with semaphore:
            started = time.perf_counter()
            try:
                out = await qr.GeminiQueryRewriter(invoke_rewrite=invoke).rewrite(case["query"], [])
                error = None
            except Exception as exc:  # 評測要記下失敗，不能讓一題炸掉整批
                out, error = None, repr(exc)[:200]
            ms = int((time.perf_counter() - started) * 1000)

        proposed = bool(str(raw_holder.get("typo_to") or "").strip())
        record = {
            "id": case["id"], "attempt": attempt, "query": case["query"], "ms": ms, "error": error,
            "raw_typo": [raw_holder.get("typo_from", ""), raw_holder.get("typo_to", "")],
            "typo_fix": list(out.typo_fix) if out and out.typo_fix else None,
            "kb_query": out.kb_query if out else None,
            "zh_terms": out.zh_terms if out else None,
            "en_terms": out.en_terms if out else None,
            "proposed": proposed,
        }
        if out is None:
            record["pass"] = False
        elif case["id"].startswith("typo"):
            corrected = case["query"].replace(*out.typo_fix) if out.typo_fix else case["query"]
            record["pass"] = bool(out.typo_fix) and case["expect"] in corrected
        else:
            text = f"{out.kb_query} {out.zh_terms}"
            record["forbidden_hit"] = [w for w in case.get("forbid", []) if w in text]
            record["pass"] = not proposed and not record["forbidden_hit"]
        return record

    records = await asyncio.gather(
        *(one(case, attempt) for case in cases for attempt in range(1, args.repeats + 1))
    )

    by_id: dict[str, list[dict]] = {}
    for r in records:
        by_id.setdefault(r["id"], []).append(r)
    print(f"model={args.model} repeats={args.repeats} calls={len(records)}")
    for group in ("typo", "clean"):
        rows = [r for r in records if r["id"].startswith(group)]
        print(f"\n== {group}：通過 {sum(r['pass'] for r in rows)}/{len(rows)}")
        for cid, rs in by_id.items():
            if not cid.startswith(group):
                continue
            passed = sum(r["pass"] for r in rs)
            rejected = sum(1 for r in rs if r["proposed"] and not r["typo_fix"])
            sample = rs[0]
            print(
                f"{cid} {passed}/{len(rs)}  被同音檢查擋下 {rejected}  "
                f"{sample['query']} → typo={sample['raw_typo']} zh={sample['zh_terms']!r}"
                + (f" 錯誤={sample['error']}" if sample["error"] else "")
                + (f" 禁詞={sample.get('forbidden_hit')}" if sample.get("forbidden_hit") else "")
            )
    ms = sorted(r["ms"] for r in records if not r["error"])
    if ms:
        print(f"\n改寫耗時：中位數 {ms[len(ms) // 2]}ms，最慢 {ms[-1]}ms")

    REPORTS.mkdir(parents=True, exist_ok=True)
    report = REPORTS / f"{time.strftime('%Y-%m-%d-%H%M')}-{args.model}.json"
    report.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"報告：{report.relative_to(ROOT)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--model", default="gemini-3.8-flash")
    parser.add_argument("--concurrency", type=int, default=4)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
