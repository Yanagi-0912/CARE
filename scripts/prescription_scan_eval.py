#!/usr/bin/env python3
"""量測藥袋掃描在中藥藥袋與成藥包裝上的實際行為。

  python scripts/prescription_scan_eval.py --out /tmp/scan.json

走正式的 `PrescriptionOcrService.recognize()`（同一份 prompt、schema 與解析），
再把辨識出的藥名丟進掃描實際使用的兩個比對器。要回答三件事：

1. 成藥包裝會不會被判成「不是藥袋」，叫使用者換一張。
2. 中醫一包混多味的藥袋，會不會被拆成 N 筆藥——拆了之後每個時段的提醒會列
   N 樣藥，長輩實際上只要吃一包。
3. 辨識出的藥名有多少通過藥證庫校驗（決定草稿能不能一鍵確認）。

golden.jsonl 每行一張圖：
  {"image": "相對 CARE 根目錄的路徑",
   "kind": "rx_bag" | "tcm_bag" | "tcm_package" | "otc_package",
   "variant": "real" | "synthetic",
   "printed_names": ["影像上印的品名"], "expected_drugs": 1, "packets_per_dose": 1,
   "source": "出處 URL"}
expected_drugs：使用者最後該得到幾筆藥；影像上商品數量有歧義時省略，不計分。
packets_per_dose 只對 tcm_bag 有意義：每次服用實際拿幾包（一包混多味時是 1）。
rx_bag 是對照組：一般醫院藥袋，確認管線在它設計的輸入上是好的，否則另外幾組
的數字沒有比較基準。synthetic 分開列，不跟真實照片混算。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional, Sequence

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from app.core.config import settings
from app.services.gemini import GeminiService
from app.services.medication.drug_catalog_service import DrugCatalogService
from app.services.medication.prescription_ocr_service import (
    PrescriptionOcrService,
    PrescriptionScanError,
)
from app.services.medication.tcm_catalog_service import (
    DEFAULT_TCM_CATALOG_PATH,
    TcmCatalogService,
)
from scripts.handwriting_eval import _mime_for

DEFAULT_GOLDEN = _PROJECT_ROOT / "evals" / "prescription_scan" / "golden.jsonl"

_REASON_LABEL = {
    "not_prescription": "判非藥袋（叫使用者換一張）",
    "unreadable": "讀不出（叫使用者重拍）",
    "service_unavailable": "服務失敗",
}

_GROUP_ORDER = ["rx_bag", "tcm_bag", "tcm_bag（合成）", "tcm_package", "otc_package"]


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else _PROJECT_ROOT / p


def _group(row: dict[str, Any]) -> str:
    return row["kind"] + ("（合成）" if row.get("variant") == "synthetic" else "")


def load_cases(golden: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in golden.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _annotate(result, drugs: DrugCatalogService, tcm: TcmCatalogService) -> dict[str, Any]:
    """把辨識結果換成報告的形狀，並補上掃描實際會做的藥名校驗。

    病患姓名只記有沒有、不記內容——樣本即使取自公開來源，報告也不該變成一份
    姓名清單。
    """
    rows = []
    for drug in result.drugs:
        match = drugs.match(drug.name)
        tcm_entry = tcm.match(drug.name)
        rows.append(
            {
                "name": drug.name,
                "frequency_code": drug.frequency_code,
                "usage_raw": drug.usage_raw,
                "timing": drug.timing,
                "verified": match is not None,
                "license_pinned": bool(match and match.license_number),
                "n_candidates": len(match.candidates) if match else 0,
                "tcm_formula": tcm_entry.name_zh if tcm_entry else None,
            }
        )
    return {
        "institution": result.institution,
        "patient_name_present": bool(result.patient_name),
        "multiple_bags_suspected": result.multiple_bags_suspected,
        "drugs": rows,
    }


async def run(cases, repeat: int, model_name: str, concurrency: int, timeout: int):
    service = GeminiService(api_key=settings.GEMINI_API_KEY, model_name=model_name)
    ocr = PrescriptionOcrService(gemini_service=service, timeout_seconds=timeout)
    drugs = DrugCatalogService.load_from_path(
        str(_resolve(settings.DRUG_CATALOG_PATH)),
        threshold=settings.DRUG_CATALOG_MATCH_THRESHOLD,
    )
    tcm = TcmCatalogService.load_from_path(str(_resolve(DEFAULT_TCM_CATALOG_PATH)))
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case, run_index):
        path = _resolve(case["image"])
        async with semaphore:
            try:
                result = await ocr.recognize(path.read_bytes(), _mime_for(path))
                pred = _annotate(result, drugs, tcm)
            except PrescriptionScanError as exc:
                pred = {"rejected": exc.reason}
            except Exception as exc:  # noqa: BLE001 - 單張失敗不該中斷整批
                pred = {"error": f"{type(exc).__name__}: {exc}"}
        print(f"  [{_group(case)}] {case['image']} #{run_index} → {_one_line(pred)}", flush=True)
        return {**case, "run": run_index, "pred": pred}

    jobs = [one(case, i) for case in cases for i in range(repeat)]
    return await asyncio.gather(*jobs)


def _one_line(pred: dict[str, Any]) -> str:
    if "rejected" in pred:
        return _REASON_LABEL.get(pred["rejected"], pred["rejected"])
    if "error" in pred:
        return f"錯誤 {pred['error']}"
    parts = []
    for d in pred["drugs"]:
        mark = "✓" if d["verified"] else "✗"
        tcm = f" 方:{d['tcm_formula']}" if d["tcm_formula"] else ""
        parts.append(f"{d['name']}({d['frequency_code']} {mark}{tcm})")
    return f"{len(pred['drugs'])} 筆：" + "、".join(parts)


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    accepted = [r for r in rows if "drugs" in r["pred"]]
    rejected = Counter(r["pred"]["rejected"] for r in rows if "rejected" in r["pred"])
    errors = [r for r in rows if "error" in r["pred"]]
    drugs = [d for r in accepted for d in r["pred"]["drugs"]]
    # 一鍵確認還要族譜比對到用藥對象，這裡量不到，所以這個數字是上限。
    one_tap = [
        r
        for r in accepted
        if r["pred"]["drugs"]
        and all(d["verified"] for d in r["pred"]["drugs"])
        and all(d["frequency_code"] != "OTHER" for d in r["pred"]["drugs"])
    ]
    with_expected = [r for r in accepted if r.get("expected_drugs")]
    count_match = [r for r in with_expected if len(r["pred"]["drugs"]) == r["expected_drugs"]]
    with_packets = [r for r in accepted if r.get("packets_per_dose")]
    split = [r for r in with_packets if len(r["pred"]["drugs"]) > r["packets_per_dose"]]
    return {
        "n": len(rows),
        "accepted": len(accepted),
        "rejected": dict(rejected),
        "errors": len(errors),
        "drugs_extracted": len(drugs),
        "names_verified": sum(d["verified"] for d in drugs),
        "license_pinned": sum(d["license_pinned"] for d in drugs),
        "tcm_formula_hits": sum(1 for d in drugs if d["tcm_formula"]),
        "frequency_other": sum(1 for d in drugs if d["frequency_code"] == "OTHER"),
        "one_tap_upper_bound": len(one_tap),
        "count_labeled": len(with_expected),
        "count_match": len(count_match),
        "packets_labeled": len(with_packets),
        "split": len(split),
        "split_cases": [
            {"image": r["image"], "run": r["run"], "extracted": len(r["pred"]["drugs"]),
             "packets_per_dose": r["packets_per_dose"]}
            for r in split
        ],
    }


def print_report(group: str, s: dict[str, Any]) -> None:
    print(f"\n=== {group}（{s['n']} 次）===")
    rejected = "｜".join(f"{_REASON_LABEL.get(k, k)} {v}" for k, v in s["rejected"].items())
    print(f"接受 {s['accepted']}" + (f"｜{rejected}" if rejected else "") + f"｜錯誤 {s['errors']}")
    if s["drugs_extracted"]:
        print(
            f"抽出藥名 {s['drugs_extracted']} 筆｜藥證庫通過 {s['names_verified']}"
            f"｜釘定證號 {s['license_pinned']}｜中藥庫方名 {s['tcm_formula_hits']}"
            f"｜頻次 OTHER {s['frequency_other']}"
        )
        print(f"可一鍵確認（上限）{s['one_tap_upper_bound']} / {s['accepted']}")
    if s["count_labeled"]:
        print(f"藥品筆數對得上 {s['count_match']} / {s['count_labeled']}")
    if s["packets_labeled"]:
        print(f"一包混多味被拆開 {s['split']} / {s['packets_labeled']}")
        for c in s["split_cases"]:
            print(f"  {c['image']} #{c['run']}：實際每次 {c['packets_per_dose']} 包 → 抽出 {c['extracted']} 筆")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--model", default=None, help="預設 settings.MODEL_NAME（正式掃描用的同一個）")
    parser.add_argument("--repeat", type=int, default=1, help="每張跑幾次，看結果穩不穩")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=settings.PRESCRIPTION_SCAN_TIMEOUT_SECONDS)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if not settings.GEMINI_API_KEY:
        raise SystemExit("缺 GEMINI_API_KEY")
    model_name = args.model or settings.MODEL_NAME
    cases = load_cases(args.golden)
    print(f"樣本 {len(cases)} 張 × {args.repeat} 次｜模型 {model_name}")

    rows = asyncio.run(run(cases, args.repeat, model_name, args.concurrency, args.timeout))

    by_group: dict[str, list] = defaultdict(list)
    for r in rows:
        by_group[_group(r)].append(r)
    report = {"model": model_name, "repeat": args.repeat, "groups": {}, "rows": rows}
    for group in _GROUP_ORDER + sorted(set(by_group) - set(_GROUP_ORDER)):
        if by_group.get(group):
            s = summarize(by_group[group])
            print_report(group, s)
            report["groups"][group] = s

    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n報告寫到 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
