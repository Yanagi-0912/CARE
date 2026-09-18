#!/usr/bin/env python3
"""驗證 cases.json 的每一組測資會落在它宣稱的規則上。

  cd CARE && python evals/interaction_alerts/verify.py

為什麼需要這支：測資的價值完全建立在「它真的打得中」。一組手寫的中藥＋西藥
組合看起來很合理，卻可能因為配對表沒登錄、藥證庫查不到成分、或品名在庫裡有
兩張證號而靜默地什麼都不觸發——那時候在 LINE 上看到沒反應，會誤以為是功能
壞了。這支在送進 LINE 之前先把這件事排除掉。

做法是重放 `OtcAlertService._check` 的判定順序（bleeding → overlap →
stacking → tcm），但不碰資料庫、不發推播：四條規則本來就是純函式，資料也
全部來自 repo 內的 `resources/`。因此這支的結論與線上一致的前提只有一個
——`resources/` 沒有換過，而那正是我們要一起檢查的。

品名另外驗一次 `DrugCatalogService.match`：藥袋上印的字串要能定出唯一證號，
偵測才拿得到成分。庫裡同名兩張證的品項會停在候選選擇，不會走到偵測。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services.medication.drug_catalog_service import DrugCatalogService  # noqa: E402
from app.services.medication.tcm_catalog_service import TcmCatalogService  # noqa: E402
from app.services.safety.atc_interaction import ClassPairTable, find_class_pair  # noqa: E402
from app.services.safety.ingredient_overlap import (  # noqa: E402
    IngredientClass,
    IngredientWatchlist,
    find_class_stacking,
    find_overlap,
    is_local_action,
    load_local_action_forms,
    should_check,
)
from app.services.safety.tcm_interaction import (  # noqa: E402
    TcmInteractionTable,
    find_tcm_interaction,
)

RES = ROOT / "resources"


class View:
    """`OtcAlertService._DrugView` 的離線等價物，欄位與取值路徑相同。"""

    def __init__(self, spec, catalog, tcm_catalog):
        self.name = spec["name"]
        entry = None
        if spec.get("license_number"):
            entry = catalog.entry_by_license_number(spec["license_number"])
        self.ingredients = tuple(getattr(entry, "ingredients", ()) or ())
        self.atc_codes = tuple(getattr(entry, "atc_codes", ()) or ())
        self.dosage_form = getattr(entry, "dosage_form", "") or ""
        self.drug_class = getattr(entry, "drug_class", "") or ""
        self.tcm_keys = ()
        if not self.ingredients:
            hit = tcm_catalog.match(self.name)
            if hit is not None:
                self.tcm_keys = hit.interaction_keys

    @property
    def is_otc(self):
        return should_check(self.drug_class)

    @property
    def is_tcm(self):
        return bool(self.tcm_keys)


def run_case(case, ctx):
    catalog, tcm_catalog, watch, anti, pairs, herbs, inter, local_forms = ctx
    existing = [View(s, catalog, tcm_catalog) for s in case["existing"]]
    added = [View(s, catalog, tcm_catalog) for s in case["added"]]

    new_otc = [v for v in added if v.is_otc or v.is_tcm]
    if not new_otc:
        return "none", "new_otc 為空（全是處方藥或查不到分級）"

    def comparable(v):
        return bool(v.ingredients or v.tcm_keys) and not is_local_action(
            v.dosage_form, local_forms
        )

    def scan(fn):
        pool = [v for v in existing if comparable(v)]
        for cand in new_otc:
            if not comparable(cand):
                pool.append(cand)
                continue
            for other in pool:
                got = fn(cand, other)
                if got:
                    return got
            pool.append(cand)
        return None

    bleeding = scan(
        lambda c, o: find_class_pair(c.atc_codes, o.atc_codes, pairs)
    )
    if bleeding:
        return "bleeding", f"{bleeding.pair_id}：{bleeding.new_label} × {bleeding.existing_label}"

    def overlap_fn(c, o):
        hit = find_overlap(c.ingredients, o.ingredients, watch)
        if hit:
            return hit.ingredients
        hit = find_overlap(c.tcm_keys[1:], o.tcm_keys[1:], herbs)
        return hit.ingredients if hit else None

    overlap = scan(overlap_fn)
    if overlap:
        return "overlap", "、".join(overlap)

    stacking = scan(
        lambda c, o: find_class_stacking(c.ingredients, o.ingredients, anti)
    )
    if stacking:
        return "stacking", f"{stacking.new_ingredient} × {stacking.existing_ingredient}"

    # 中西藥兩個方向都看，比對池含這次提交裡先前的藥。
    pool = list(existing)
    for cand in new_otc:
        for other in pool:
            for tcm_side, west_side in ((cand, other), (other, cand)):
                if not tcm_side.tcm_keys or not west_side.ingredients:
                    continue
                if not comparable(west_side):
                    continue
                hit = find_tcm_interaction(tcm_side.tcm_keys, west_side.ingredients, inter)
                if hit:
                    return "tcm", f"{hit.tcm_name} × {hit.western_ingredient}"
        pool.append(cand)
    return "none", "四條規則都沒成立"


def main():
    catalog = DrugCatalogService.load_from_path(str(RES / "drug_catalog.json"), 0.88)
    tcm_catalog = TcmCatalogService.load_from_path(str(RES / "tcm_catalog.json"))
    ctx = (
        catalog,
        tcm_catalog,
        IngredientWatchlist.load_from_path(str(RES / "otc_watch_ingredients.json")),
        IngredientClass.load_from_path(str(RES / "anticholinergic_ingredients.json")),
        ClassPairTable.load_from_path(str(RES / "interaction_class_pairs.json")),
        IngredientWatchlist(
            e["name"]
            for e in json.loads((RES / "tcm_watch_herbs.json").read_text("utf-8"))["herbs"]
        ),
        TcmInteractionTable.load_from_path(str(RES / "tcm_drug_interactions.json")),
        load_local_action_forms(str(RES / "otc_watch_ingredients.json")),
    )

    data = json.loads(Path(__file__).with_name("cases.json").read_text("utf-8"))
    failed = 0
    for case in data["cases"]:
        got, detail = run_case(case, ctx)
        want = case["expect"]["rule"]
        ok = got == want
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {case['id']:<32} 期望 {want:<9} 實得 {got:<9} {detail}")
        # 藥袋上的品名要定得出唯一證號，偵測才拿得到成分。
        for spec in case["existing"] + case["added"]:
            if spec.get("kind") == "tcm":
                if tcm_catalog.match(spec["name"]) is None:
                    print(f"      ⚠ 中藥庫認不出方名：{spec['name']}")
                    failed += 1
                continue
            match = catalog.match(spec["name"])
            got_license = getattr(match, "license_number", None)
            if got_license != spec["license_number"]:
                n = len(getattr(match, "candidates", []) or [])
                print(f"      ⚠ 品名定不出唯一證號：{spec['name']}（候選 {n} 張，得到 {got_license}）")
                failed += 1

    print(f"\n{len(data['cases'])} 組，{failed} 個問題")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
