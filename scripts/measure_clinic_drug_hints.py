"""量看診逐字稿藥名標示的門檻（`drug_hints.MATCH_THRESHOLD` 的依據）。

不打任何 API，只用本機的 `resources/drug_catalog.json`。

### 量什麼

看診逐字稿放棄了熱詞，所以藥名一定有聽錯的字。這裡模擬那個情境，問兩件事：

- **標錯藥**：逐字稿在講 A 藥，我們卻標成長輩清單上的 B 藥。這是有害的失敗，
  家人會以為醫師在講另一顆。
- **漏標**：逐字稿在講 A 藥、A 藥也在清單上，我們沒標出來。這是無害的失敗，
  家人只是少一個提示，逐字稿原文照樣看得到。

兩者不對稱，所以門檻要往「寧可漏標」那邊偏。

### 怎麼模擬

從真實藥證庫隨機抽 8 顆當一位長輩的用藥清單（CARE 實際使用者的清單多在個位數），
挑其中一顆當「這次看診講到的藥」，用兩種方式弄壞它來模擬語音辨識的錯字：

- 換掉一個中文字（`char`）
- 吞掉一個中文字（`drop`）

再把弄壞的藥名塞進一句像門診會講的話，跑 `find_hints()`，看標出來的是不是原來那顆。

跑法：`uv run python scripts/measure_clinic_drug_hints.py`
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.clinic_transcript.drug_hints import (  # noqa: E402
    MIN_KEY_LENGTH,
    _strip_dose,
    find_hints,
)

CATALOG = Path(__file__).resolve().parents[1] / "resources" / "drug_catalog.json"
SEED = 20260916
SAMPLES = 3000
LIST_SIZE = 8
SENTENCE = "那個{name}我們先停掉，兩個禮拜後再回來看"


def load_names() -> list[str]:
    entries = json.loads(CATALOG.read_text(encoding="utf-8"))
    names = []
    for entry in entries:
        name = (entry.get("name_zh") or "").strip()
        # 太短的本來就不比對，放進母體只會稀釋結果。
        if name and len(_strip_dose(name)) >= MIN_KEY_LENGTH:
            names.append(name)
    return sorted(set(names))


def corrupt(name: str, mode: str, rng: random.Random, charset: str) -> str | None:
    positions = [i for i, ch in enumerate(name) if "一" <= ch <= "鿿"]
    if not positions:
        return None
    index = rng.choice(positions)
    if mode == "drop":
        return name[:index] + name[index + 1 :]
    replacement = rng.choice(charset)
    while replacement == name[index]:
        replacement = rng.choice(charset)
    return name[:index] + replacement + name[index + 1 :]


def main() -> None:
    names = load_names()
    charset = "".join(sorted({ch for n in names for ch in n if "一" <= ch <= "鿿"}))
    print(f"母體 {len(names)} 個品名，用字 {len(charset)} 個，種子 {SEED}\n")

    thresholds = [0.70, 0.75, 0.78, 0.80, 0.82, 0.85, 0.88, 0.90]
    for mode in ("char", "drop"):
        rng = random.Random(SEED)
        cases = []
        for _ in range(SAMPLES):
            drug_list = rng.sample(names, LIST_SIZE)
            target = rng.choice(drug_list)
            broken = corrupt(target, mode, rng, charset)
            if broken is None:
                continue
            cases.append((SENTENCE.format(name=broken), drug_list, target))

        print(f"--- 弄壞方式：{mode}（{len(cases)} 筆）---")
        print(f"{'門檻':>6} {'標對':>8} {'標錯藥':>8} {'漏標':>8}")
        for threshold in thresholds:
            right = wrong = missed = 0
            for text, drug_list, target in cases:
                hits = find_hints(text, drug_list, threshold=threshold)
                found = {hit.medication_name for hit in hits}
                if not found:
                    missed += 1
                elif found == {target}:
                    right += 1
                else:
                    # 標到別顆藥，或同時標了好幾顆——兩種都會讓家人看錯。
                    wrong += 1
            total = len(cases)
            print(
                f"{threshold:>6.2f} {right / total:>7.1%} {wrong / total:>8.2%} "
                f"{missed / total:>7.1%}"
            )
        print()


if __name__ == "__main__":
    main()
