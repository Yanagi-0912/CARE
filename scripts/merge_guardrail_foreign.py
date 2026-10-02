#!/usr/bin/env python3
"""把急迫度資料集裡的外語列換成 guardrail 標籤，併進 guardrail 資料集。

**為什麼要補外語。** guardrail 資料集原本只有中文（2,998 筆全有漢字），英、印尼、
越、泰、日文的片段模型幾乎沒看過；看過的英文片段又多半來自寫程式、購物這類中文句
裡夾的英文，被學成「不是健康問題」的訊號。2026-09-15 用外語 holdout 量舊模型：英文
198 題健康問題被本地直接擋掉 195 題、印尼文 158 題、越南文 61 題，使用者拿不到知識庫
的答案（一則英文語音「I have a stomach ache and my knee is hurt.」就是這樣變成一般回覆）。

**為什麼不必重新生成。** `evals/urgency/dataset.jsonl` 已經有五種外語各 1,800 筆，
是用 guardrail 同一份 bucket 描述（`guardrail:*`）與急迫度的 bucket 生成的，標籤是
「緊不緊急」。這裡依 bucket 的內容換成 guardrail 的「是否與健康醫療相關」：
guardrail 資料集本來就是依 bucket 標標籤，換過來的列與中文列是同一種標法。

內容明確是身體狀況、疾病或心理健康的急迫度 bucket 算相關；標籤不明確的排除，不硬標：
急症的行政問題（收費、理賠，貼近 guardrail 的 adjacent_but_no）、把「想死」當誇飾、
轉述作品或他人舊事、沒有明說的道別。

split 沿用急迫度資料集的切法，holdout 仍是模型沒看過的資料。重複執行會先移除上一次
併入的列（`source == "urgency_dataset"`），中文列不動。

**標籤更正（`evals/guardrail/label_overrides.jsonl`）。** 依 bucket 標標籤的前提是生成
的句子真的落在那個 bucket，外語這批常常不是：寫「財經新聞」「購物旅遊」的句子裡夾著
「股票跌到偏頭痛，吃普拿疼沒好」這類健康問題，卻照 bucket 標成 0。2026-10-02 用 5 折
交叉驗證找出模型有把握、但與標籤相反的 481 列逐列複核（Claude 複核，非人工），107 列
改標 1、48 列兩可移除，見該檔的 reason 欄。更正以原文為鍵、在最後對全部列套用，所以
重跑本腳本不會把它洗掉。

用法（專案根目錄）：
  python scripts/merge_guardrail_foreign.py
  python scripts/build_guardrail_model.py --max-false-alarm-rate 0.10
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUARDRAIL_DATASET = _PROJECT_ROOT / "evals" / "guardrail" / "dataset.jsonl"
URGENCY_DATASET = _PROJECT_ROOT / "evals" / "urgency" / "dataset.jsonl"
LABEL_OVERRIDES = _PROJECT_ROOT / "evals" / "guardrail" / "label_overrides.jsonl"
SOURCE = "urgency_dataset"
PRIMARY_LANGUAGE = "zh-TW"

# 急迫度 bucket → guardrail 標籤。沒列出的 bucket 不併入。
GUARDRAIL_LABELS: dict[str, int] = {
    # guardrail 自己的 bucket，沿用 scripts/build_guardrail_dataset.py 的標籤
    "guardrail:common_disease": 1,
    "guardrail:medication": 1,
    "guardrail:nutrition_exercise": 1,
    "guardrail:scam_health": 1,
    "guardrail:smalltalk": 0,
    "guardrail:daily_task": 0,
    "guardrail:news_finance": 0,
    "guardrail:food_no_health": 0,
    "guardrail:shopping_travel": 0,
    "guardrail:bot_meta": 0,
    "guardrail:adjacent_but_no": 0,
    # 急迫度的 bucket：急症、症狀、急症知識與後續照護、自傷意念都是健康或心理健康
    "unconscious_collapse": 1,
    "breathing_chest": 1,
    "stroke_signs": 1,
    "trauma_bleeding": 1,
    "poisoning_overdose": 1,
    "other_acute": 1,
    "colloquial_emergency": 1,
    "self_harm": 1,
    "knowledge_about_emergency": 1,
    "past_followup": 1,
    "non_urgent_symptom": 1,
    "ordinary_farewell": 0,
}


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _apply_overrides(rows: list[dict]) -> tuple[list[dict], int, int]:
    """label 為 null 的列移除，其餘改成更正後的標籤。中文列已更正過，再套一次結果不變。"""
    if not LABEL_OVERRIDES.exists():
        return rows, 0, 0
    overrides = {o["text"]: o["label"] for o in _read(LABEL_OVERRIDES)}
    out, relabeled, dropped = [], 0, 0
    for row in rows:
        if row["text"] not in overrides:
            out.append(row)
        elif overrides[row["text"]] is None:
            dropped += 1
        else:
            relabeled += int(row["label"] != overrides[row["text"]])
            out.append({**row, "label": overrides[row["text"]]})
    return out, relabeled, dropped


def main() -> int:
    kept = [row for row in _read(GUARDRAIL_DATASET) if row.get("source") != SOURCE]
    added = []
    for row in _read(URGENCY_DATASET):
        if row["lang"] == PRIMARY_LANGUAGE or row["bucket"] not in GUARDRAIL_LABELS:
            continue
        added.append(
            {
                "text": row["text"],
                "label": GUARDRAIL_LABELS[row["bucket"]],
                "bucket": row["bucket"],
                "lang": row["lang"],
                "split": row["split"],
                "source": SOURCE,
                "model": row.get("model"),
                "generated_at": row.get("generated_at"),
            }
        )
    rows, relabeled, dropped = _apply_overrides(kept + added)
    GUARDRAIL_DATASET.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    print(f"中文與既有列 {len(kept)}，併入外語 {len(added)}；更正改標 {relabeled}、移除 {dropped}，共 {len(rows)}")
    for (lang, label), n in sorted(Counter((r["lang"], r["label"]) for r in added).items()):
        print(f"  {lang} label={label}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
