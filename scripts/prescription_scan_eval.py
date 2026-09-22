#!/usr/bin/env python3
"""量測藥袋掃描在中藥藥袋與成藥包裝上的實際行為。

  python scripts/prescription_scan_eval.py --out /tmp/scan.json

走正式的 `PrescriptionOcrService.recognize()`（同一份 prompt、schema 與解析），
再把辨識出的藥名丟進掃描實際使用的兩個比對器。要回答三件事：

1. 成藥包裝會不會被判成「不是藥袋」，叫使用者換一張。
2. 中醫一包混多味的藥袋，會不會被拆成 N 筆藥——拆了之後每個時段的提醒會列
   N 樣藥，長輩實際上只要吃一包。
3. 辨識出的藥名有多少通過藥證庫校驗（決定草稿能不能一鍵確認）。
4. 釘選藥證之後，相衝偵測（`OtcAlertService`）有沒有東西可比——每一筆藥
   走到藥證庫的成分、劑型、ATC 與中藥庫方名之後，四條規則裡有沒有任何一條
   的**單側**條件成立。這不是「會不會發警報」（那要看當事人正在吃什麼），
   而是「這筆藥有沒有資格觸發任何一條」。沒資格的藥，不論長輩家裡還有什麼，
   相衝偵測都是靜默的；這一欄量的就是那個漏洞有多大。

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
from app.services.safety.atc_interaction import ClassPairTable
from app.services.safety.ingredient_overlap import (
    IngredientClass,
    IngredientWatchlist,
    is_local_action,
    load_local_action_forms,
    should_check,
)
from app.services.safety.tcm_interaction import TcmInteractionTable, normalize_tcm_name
from scripts.handwriting_eval import _mime_for

DEFAULT_GOLDEN = _PROJECT_ROOT / "evals" / "prescription_scan" / "golden.jsonl"

_REASON_LABEL = {
    "not_prescription": "判非藥袋（叫使用者換一張）",
    "unreadable": "讀不出（叫使用者重拍）",
    "service_unavailable": "服務失敗",
}

_GROUP_ORDER = ["rx_bag", "tcm_bag", "tcm_bag（合成）", "tcm_package", "otc_package", "otc_package（合成）"]


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


class _Rules:
    """相衝偵測讀的四份資料表，與正式服務同一組檔案（`app/dependencies.py`）。"""

    def __init__(self) -> None:
        self.watchlist = IngredientWatchlist.load_from_path()
        self.anticholinergics = IngredientClass.load_from_path()
        self.class_pairs = ClassPairTable.load_from_path()
        self.tcm_interactions = TcmInteractionTable.load_from_path()
        self.local_forms = load_local_action_forms()


def _pinnable_entries(match, drugs: DrugCatalogService) -> list:
    """釘選後可能落地的藥證。

    唯一命中時掃描已經自動釘好，只有那一張。多張候選時使用者會在核對畫面
    挑一張——**哪一張都可能**，所以全部列出、逐張算，而不是挑一張代表
    （挑了就是 `DrugCatalogMatch` 文件裡說的「編造」）。查無回空。
    """
    if match is None:
        return []
    if match.license_number:
        entry = drugs.entry_by_license_number(match.license_number)
        return [entry] if entry is not None else []
    return list(match.candidates)


def _rule_capability(entry, tcm_entry, rules: _Rules) -> dict[str, Any]:
    """這筆藥釘選之後，四條規則各自的單側條件成立嗎。

    對應 `OtcAlertService`：觸發側要是成藥或中藥（`should_check` / 中藥庫命中），
    局部作用劑型不進比對，然後——
      成分重複   ：成分有落在白名單上的
      抗膽鹼疊加 ：成分有落在抗膽鹼清單上的
      出血       ：ATC 碼命中任一組類別配對的任一側
      中西藥     ：中藥庫方名（含組成藥材）在交互作用表裡有登錄
    """
    ingredients = tuple(getattr(entry, "ingredients", ()) or ())
    atc_codes = tuple(getattr(entry, "atc_codes", ()) or ())
    dosage_form = getattr(entry, "dosage_form", "") or ""
    drug_class = getattr(entry, "drug_class", "") or ""
    tcm_keys = tcm_entry.interaction_keys if tcm_entry is not None else ()

    watched = sorted(i for i in ingredients if i in rules.watchlist)
    anticholinergic = sorted(i for i in ingredients if i in rules.anticholinergics)
    class_sides = []
    for pair in rules.class_pairs._pairs:
        if pair.a.matches(atc_codes):
            class_sides.append(f"{pair.pair_id}:{pair.a.label_zh}")
        if not pair.is_self_pair and pair.b.matches(atc_codes):
            class_sides.append(f"{pair.pair_id}:{pair.b.label_zh}")
    tcm_listed = any(
        rules.tcm_interactions._by_tcm.get(normalize_tcm_name(k)) for k in tcm_keys
    )
    is_trigger_side = should_check(drug_class) or bool(tcm_keys)
    local = is_local_action(dosage_form, rules.local_forms)
    capable = is_trigger_side and not local and bool(
        watched or anticholinergic or class_sides or tcm_listed
    )
    return {
        "pinned_class": drug_class,
        "trigger_side": is_trigger_side,
        "local_action_form": local,
        "watched_ingredients": watched,
        "anticholinergic_ingredients": anticholinergic,
        "class_pair_sides": class_sides,
        "tcm_interaction_listed": tcm_listed,
        "rule_capable": capable,
    }


def _annotate(
    result, drugs: DrugCatalogService, tcm: TcmCatalogService, rules: _Rules
) -> dict[str, Any]:
    """把辨識結果換成報告的形狀，並補上掃描實際會做的藥名校驗。

    病患姓名只記有沒有、不記內容——樣本即使取自公開來源，報告也不該變成一份
    姓名清單。
    """
    rows = []
    for drug in result.drugs:
        match = drugs.match(drug.name)
        tcm_entry = tcm.match(drug.name)
        # 與 `OtcAlertService._to_view` 同一個規則：西藥藥證庫查得到成分就不
        # 再問中藥庫。多張候選逐張算：使用者挑哪一張都要能比，這筆藥才算
        # 「釘了就有規則可比」；只有部分候選可比的另外標出來。
        entries = _pinnable_entries(match, drugs)
        per_candidate = [
            _rule_capability(e, tcm_entry if not getattr(e, "ingredients", None) else None, rules)
            for e in entries
        ] or ([_rule_capability(None, tcm_entry, rules)] if tcm_entry is not None else [])
        capable_flags = [c["rule_capable"] for c in per_candidate]
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
                # 釘選後有藥證（或中藥方）可落地；空＝查無，釘不了
                "pinnable": bool(per_candidate),
                # 逐張候選的規則資格；rules 取「每一張都可比」的合取
                "rule_capable_by_candidate": [
                    {"license_number": getattr(e, "license_number", None), **c}
                    for e, c in zip(entries or [None], per_candidate)
                ],
                "rules": {
                    "rule_capable": bool(capable_flags) and all(capable_flags),
                    "rule_capable_partial": any(capable_flags) and not all(capable_flags),
                    "watched_ingredients": sorted({i for c in per_candidate for i in c["watched_ingredients"]}),
                    "anticholinergic_ingredients": sorted({i for c in per_candidate for i in c["anticholinergic_ingredients"]}),
                    "class_pair_sides": sorted({i for c in per_candidate for i in c["class_pair_sides"]}),
                    "tcm_interaction_listed": any(c["tcm_interaction_listed"] for c in per_candidate),
                },
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
    rules = _Rules()
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case, run_index):
        path = _resolve(case["image"])
        async with semaphore:
            try:
                result = await ocr.recognize(path.read_bytes(), _mime_for(path))
                pred = _annotate(result, drugs, tcm, rules)
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
        rules = d.get("rules") or {}
        if rules.get("rule_capable"):
            clash = " 相衝:可比"
        elif rules.get("rule_capable_partial"):
            clash = " 相衝:看挑哪張候選"
        elif d.get("pinnable"):
            clash = " 相衝:釘了也沒規則"
        else:
            clash = " 相衝:釘不了"
        parts.append(f"{d['name']}({d['frequency_code']} {mark}{tcm}{clash})")
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
        # 相衝偵測的漏斗：抽出 → 釘得上藥證 → 釘上後有規則可比。差額就是
        # 「不論長輩家裡還有什麼，相衝偵測都靜默」的那些藥。
        "pinnable": sum(1 for d in drugs if d.get("pinnable")),
        "rule_capable": sum(1 for d in drugs if (d.get("rules") or {}).get("rule_capable")),
        "rule_capable_partial": sum(1 for d in drugs if (d.get("rules") or {}).get("rule_capable_partial")),
        "rule_capable_by": {
            "overlap": sum(1 for d in drugs if (d.get("rules") or {}).get("watched_ingredients")),
            "stacking": sum(1 for d in drugs if (d.get("rules") or {}).get("anticholinergic_ingredients")),
            "bleeding": sum(1 for d in drugs if (d.get("rules") or {}).get("class_pair_sides")),
            "tcm": sum(1 for d in drugs if (d.get("rules") or {}).get("tcm_interaction_listed")),
        },
        "silent_drugs": [
            {"image": r["image"].split("/")[-1], "name": d["name"],
             "why": ("釘不了" if not d.get("pinnable")
                     else ("看挑哪張候選" if (d.get("rules") or {}).get("rule_capable_partial")
                           else "釘了也沒規則"))}
            for r in accepted for d in r["pred"]["drugs"]
            if not (d.get("rules") or {}).get("rule_capable")
        ],
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
        by = s["rule_capable_by"]
        print(
            f"相衝偵測漏斗：抽出 {s['drugs_extracted']} → 釘得上 {s['pinnable']}"
            f" → 有規則可比 {s['rule_capable']}（另 {s['rule_capable_partial']} 筆看挑哪張候選）"
            f"（重複 {by['overlap']}｜疊加 {by['stacking']}｜出血 {by['bleeding']}｜中西藥 {by['tcm']}）"
        )
        for d in s["silent_drugs"]:
            print(f"  靜默：{d['image']}／{d['name']}（{d['why']}）")
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
