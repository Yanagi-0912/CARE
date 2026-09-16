#!/usr/bin/env python3
"""評測走失求救的判斷（關鍵字＋本地分類器）：逐語言看漏判、誤判、附按鈕的比例。

只看 holdout（模型沒看過的資料），三類訊息分開算，理由同 `scripts/urgency_eval.py`：

  - positive：真的走失。**漏判**＝落到 low 以下、被當成一般訊息，沒有人被通知。
  - hard_negative：講到迷路、位置但不是本人此刻走丟。**誤判**＝直接通知家人。
  - everyday：沿用的一般訊息（閒聊、用藥、症狀、急症），真實流量的大宗。
    它的「附按鈕」比例決定一般使用者多常在回覆下方看到「我迷路了，通知家人」。

判斷走的是正式路徑的 `LostIntentDetector`（關鍵字先判），不是只看分類器。

用法（專案根目錄）：
  uv run python scripts/lost_eval.py
  uv run python scripts/lost_eval.py --model /tmp/lost_model_20k.json
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

from app.services.guardrail.local import LocalGuardrailClassifier  # noqa: E402
from app.services.lost.lost_classifier import LOST_MODEL_PATH, LostIntentDetector  # noqa: E402

DEFAULT_DATASET = _PROJECT_ROOT / "evals" / "lost" / "dataset.jsonl"


def _kind(row: dict) -> str:
    if row["label"] == 1:
        return "positive"
    return "everyday" if str(row.get("bucket", "")).startswith("reused:") else "hard_negative"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", type=Path, default=LOST_MODEL_PATH)
    parser.add_argument("--show", type=int, default=8, help="每類列出幾則錯誤的例子")
    args = parser.parse_args(argv)

    detector = LostIntentDetector(LocalGuardrailClassifier.load(args.model))
    # (語言, 類別) → {"n", "auto", "confirm", "none"}
    table: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_bucket: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    mistakes: dict[str, list[str]] = defaultdict(list)

    for line in args.dataset.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("split") != "holdout":
            continue
        kind = _kind(row)
        detection = detector.detect(row["text"])
        outcome = (
            "none" if detection.intent is None
            else "confirm" if detection.needs_confirmation
            else "auto"
        )
        for key in ((row["lang"], kind), ("ALL", kind)):
            table[key]["n"] += 1
            table[key][outcome] += 1
        bucket = row["bucket"] if kind != "everyday" else "everyday"
        by_bucket[bucket]["n"] += 1
        by_bucket[bucket][outcome] += 1
        if kind == "positive" and outcome == "none":
            mistakes["漏判"].append(f"[{row['lang']}] {row['text']}")
        elif kind != "positive" and outcome == "auto":
            mistakes["誤判（直接通知家人）"].append(f"[{row['lang']}/{row['bucket']}] {row['text']}")

    def pct(part: int, whole: int) -> str:
        return f"{part / whole:6.1%}" if whole else "     -"

    print("語言    類別            筆數   直接通報   附按鈕     當一般訊息")
    languages = sorted({lang for lang, _ in table if lang != "ALL"}) + ["ALL"]
    for lang in languages:
        for kind in ("positive", "hard_negative", "everyday"):
            cell = table.get((lang, kind))
            if not cell:
                continue
            n = cell["n"]
            print(
                f"{lang:<7} {kind:<14} {n:>5}   {pct(cell['auto'], n)}    "
                f"{pct(cell['confirm'], n)}    {pct(cell['none'], n)}"
            )

    print("\n各 bucket：")
    for bucket, cell in sorted(by_bucket.items()):
        n = cell["n"]
        print(
            f"  {bucket:<26} {n:>5}  直接 {pct(cell['auto'], n)}  "
            f"按鈕 {pct(cell['confirm'], n)}  一般 {pct(cell['none'], n)}"
        )

    for title, rows in mistakes.items():
        print(f"\n{title}：共 {len(rows)} 則")
        for text in rows[: args.show]:
            print(f"  {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
