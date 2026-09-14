#!/usr/bin/env python3
"""評測急迫度本地模型：逐語言、逐類訊息的本地放行率與漏判。

為什麼要拆開看：本地模型只負責「放行明顯不緊急的訊息」，錯放一則急症就沒有紅卡、
也沒有家人通報，所以每種語言的急症都要各自確認漏判是 0，不能被整體平均蓋掉。
負例也分兩類：

  - everyday：閒聊、日常請求、用藥與慢性病問題（bucket 以 `guardrail:` 開頭），
    是真實流量的大宗，它的放行率決定多數訊息要不要多等一次 Gemini。
  - hard_negative：講到急症詞但此刻不緊急（衛教問句、誇飾、談論新聞），本來就該
    多半升級給 LLM。

兩類混在一起，就會拿外語困難負例的放行率去跟中文日常訊息比——這支腳本出現之前
的「外語非急症 30.5%」就是這樣來的。

另外模擬 LLM 中斷：沒被放行的訊息改看本地機率，達到 `LOCAL_FALLBACK_CUTOFF` 就
出紅卡（與 urgency.py 的 `_when_llm_unavailable` 同一條規則），報告急症仍出紅卡、
非急症誤出紅卡的比例。

用法（專案根目錄）：
  uv run python scripts/urgency_eval.py
  uv run python scripts/urgency_eval.py --model /tmp/urgency_model_old.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.services.guardrail.local import LocalGuardrailClassifier  # noqa: E402
from app.services.medical.symptom_classification.urgency import (  # noqa: E402
    LOCAL_FALLBACK_CUTOFF,
    URGENCY_MODEL_PATH,
)

DEFAULT_DATASET = _PROJECT_ROOT / "evals" / "urgency" / "dataset.jsonl"


@dataclass
class Tally:
    total: int = 0
    # 本地直接放行（p < low）。對 emergency 來說這就是漏判。
    released: int = 0
    red_card_if_llm_down: int = 0


def _kind(row: dict[str, Any]) -> str:
    if int(row["label"]) == 1:
        return "emergency"
    return "everyday" if str(row["bucket"]).startswith("guardrail:") else "hard_negative"


def summarize(
    rows: Iterable[dict[str, Any]],
    probability: Callable[[str], float],
    low: float,
) -> dict[tuple[str, str], Tally]:
    """依 (語言, 訊息類別) 計數。只算 holdout：train 列是模型看過的資料。"""
    summary: dict[tuple[str, str], Tally] = defaultdict(Tally)
    for row in rows:
        if row.get("split") != "holdout":
            continue
        p = probability(row["text"])
        tally = summary[(row["lang"], _kind(row))]
        tally.total += 1
        if p < low:
            tally.released += 1
        elif p >= LOCAL_FALLBACK_CUTOFF:
            tally.red_card_if_llm_down += 1
    return dict(summary)


def _rate(part: int, whole: int) -> str:
    return f"{part / whole:.1%}（{part}/{whole}）" if whole else "-"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--model", type=Path, default=URGENCY_MODEL_PATH)
    args = parser.parse_args(argv)

    model = LocalGuardrailClassifier.load(args.model)
    rows = [
        json.loads(line)
        for line in args.dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    summary = summarize(rows, model.probability, model.low)

    print(f"模型 {args.model}（low={model.low:.4f}），{args.dataset} 的 holdout\n")
    print("語言    急症被本地放行（漏判）  困難負例本地放行  日常訊息本地放行")
    empty = Tally()
    for lang in sorted({lang for lang, _ in summary}):
        emergency = summary.get((lang, "emergency"), empty)
        hard = summary.get((lang, "hard_negative"), empty)
        everyday = summary.get((lang, "everyday"), empty)
        print(
            f"{lang:<6}  {emergency.released}/{emergency.total:<20}"
            f"  {_rate(hard.released, hard.total):<16}"
            f"  {_rate(everyday.released, everyday.total)}"
        )

    emergencies = [t for (_, kind), t in summary.items() if kind == "emergency"]
    negatives = [t for (_, kind), t in summary.items() if kind != "emergency"]
    red = sum(t.red_card_if_llm_down for t in emergencies)
    false_red = sum(t.red_card_if_llm_down for t in negatives)
    print(
        f"\n模擬 LLM 中斷：急症 {_rate(red, sum(t.total for t in emergencies))} 仍出紅卡，"
        f"非急症 {_rate(false_red, sum(t.total for t in negatives))} 誤出紅卡"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
