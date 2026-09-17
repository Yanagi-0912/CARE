#!/usr/bin/env python3
"""評測「直接送 RAG」捷徑：逐語言看捷徑命中多少、送錯多少。

只看 holdout（模型沒看過的資料），訊息分四類：

  - agent_rag：agent 最後走 RAG 的健康問題。**捷徑率**＝本地 >= high 的比例，
    也就是能省掉那次選工具呼叫的比例。
  - agent_other：agent 選了別的工具（掛哪科、查吃藥、查證…）。**送錯**＝本地
    >= high，使用者會拿到 RAG 答案而不是該有的卡片。
  - hard_negative：生成的困難負例（見 build_rag_route_dataset.BUCKETS），逐
    bucket 列出送錯數。
  - everyday：guardrail 資料集的非健康訊息。線上 guardrail 放行前就擋掉了，
    列出來只是確認模型沒有把「健康」以外的東西學成 RAG。

判斷只看分類器本身；線上在它之前還有找附近院所、指名院所、官網、上傳文件等
決定性規則（nodes._can_send_original_text_to_rag），這裡不重算，數字偏保守。

用法（專案根目錄）：
  python scripts/rag_route_eval.py
  python scripts/rag_route_eval.py --model /tmp/rag_route_model.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.services.agent.utils.nodes import RAG_ROUTE_MODEL_PATH  # noqa: E402
from app.services.guardrail.local import LocalGuardrailClassifier  # noqa: E402

DEFAULT_DATASET = _PROJECT_ROOT / "evals" / "rag_route" / "dataset.jsonl"


def _kind(row: dict) -> str:
    bucket = str(row.get("bucket", ""))
    if bucket.startswith("everyday:"):
        return "everyday"
    if not bucket.startswith("reused:"):
        return "hard_negative"
    return "agent_rag" if row["label"] == 1 else "agent_other"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", type=Path, default=RAG_ROUTE_MODEL_PATH)
    parser.add_argument("--show", type=int, default=15, help="列出幾則送錯的原句")
    args = parser.parse_args(argv)

    router = LocalGuardrailClassifier.load(args.model)
    print(f"model {args.model}  high={router.high:.4f}\n")

    rows = [
        json.loads(line)
        for line in args.dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    holdout = [r for r in rows if r.get("split") == "holdout"]

    # (語言, 類別) -> [總數, >= high 數]
    table: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    per_bucket: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    wrong: list[tuple[float, str, str, str]] = []
    for row in holdout:
        kind = _kind(row)
        p = router.probability(row["text"])
        hit = p >= router.high
        cell = table[(row["lang"], kind)]
        cell[0] += 1
        cell[1] += int(hit)
        if kind == "hard_negative":
            per_bucket[row["bucket"]][0] += 1
            per_bucket[row["bucket"]][1] += int(hit)
        if hit and row["label"] == 0:
            detail = ",".join(row.get("agent_calls") or []) or row["bucket"]
            wrong.append((p, row["lang"], detail, row["text"]))

    languages = sorted({lang for lang, _ in table})
    kinds = ("agent_rag", "agent_other", "hard_negative", "everyday")
    header = f"{'lang':<6}" + "".join(f"{k:>22}" for k in kinds)
    print("holdout：>= high 的則數 / 總數（agent_rag 越高越好，其餘越低越好）")
    print(header)
    totals = {k: [0, 0] for k in kinds}
    for lang in languages:
        line = f"{lang:<6}"
        for kind in kinds:
            n, hit = table.get((lang, kind), [0, 0])
            totals[kind][0] += n
            totals[kind][1] += hit
            line += f"{f'{hit}/{n}' + (f' ({hit / n:.1%})' if n else ''):>22}"
        print(line)
    line = f"{'all':<6}"
    for kind in kinds:
        n, hit = totals[kind]
        line += f"{f'{hit}/{n}' + (f' ({hit / n:.1%})' if n else ''):>22}"
    print(line)

    print("\n困難負例逐 bucket 送錯：")
    for bucket, (n, hit) in sorted(per_bucket.items()):
        print(f"  {bucket:<26} {hit}/{n}")

    if wrong:
        print(f"\n送錯的原句（機率最高的 {args.show} 則）：")
        for p, lang, detail, text in sorted(wrong, reverse=True)[: args.show]:
            print(f"  {p:.3f} {lang:<5} [{detail}] {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
