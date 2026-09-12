"""以 ATC 藥理治療分類碼判定的交互作用。

純函式與純資料，比照 `ingredient_overlap` 與 `tcm_interaction` 的分工。

## 為什麼要類別層級

`find_overlap` 與 `find_class_stacking` 都在成分層級運作，那對「同一個成分吃了
兩份」「不同成分但作用相同」是夠的。但有些風險的單位是**藥理類別**：出血風險
不是某兩個成分的事，是「任一種消炎止痛藥」配上「任一種抗凝血或抗血小板藥」。
用成分清單表達要窮舉上百組配對，用 ATC 前綴只要兩行。

這是 `drug_catalog.json` 加上 `atc_codes` 的直接用途（見
`scripts/build_drug_catalog._index_atc_codes`）。

## 前綴比對與粒度

ATC 是階層碼，前綴比對就是「屬不屬於這個類別」：`M01A` 涵蓋所有 NSAID、
`B01A` 涵蓋所有抗血栓藥。但實測資料的粒度不一致——以藥證計 99% 至少有一個
≥5 碼、93% 至少有一個 7 碼，仍有約 1% 只掛到 3～4 碼的群組層級，那 1% 用
5 碼前綴就比不到。那是資料本身的粒度，不是比對邏輯的缺陷。

一張藥證可能有多個 ATC 碼（複方藥、或同一藥有多種用途），**任一個命中即算**：
複方藥的次要成分正是交互作用要看的東西。

## 覆蓋率的天花板

`atc_codes` 的覆蓋率是 58.4%（66,503 張藥證中 38,815 張查得到）。查無 ATC 的
藥在本模組眼中不屬於任何類別，因此不會觸發任何配對——退化方向是少偵測，
不是誤報。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

DEFAULT_CLASS_PAIRS_PATH = "resources/interaction_class_pairs.json"


@dataclass(frozen=True)
class ClassSide:
    """配對的一側：一組 ATC 前綴，加一個給使用者看的中文類別名。"""

    label_zh: str
    prefixes: tuple[str, ...]

    def matches(self, atc_codes: Iterable[str]) -> bool:
        codes = [c.strip().upper() for c in atc_codes if c and c.strip()]
        return any(
            code.startswith(prefix) for code in codes for prefix in self.prefixes
        )


@dataclass(frozen=True)
class ClassPair:
    pair_id: str
    a: ClassSide
    b: ClassSide
    risk_zh: str

    @property
    def is_self_pair(self) -> bool:
        """同一類別配自己（兩種 NSAID）。這種配對兩邊都要命中同一組前綴。"""
        return self.a.prefixes == self.b.prefixes


@dataclass(frozen=True)
class ClassPairFinding:
    """一組成立的類別配對。

    `new_label` 與 `existing_label` 是中文類別名——訊息要講的是「你買的止痛藥
    和你正在吃的抗凝血藥」，講 ATC 代碼沒有人看得懂。
    """

    pair_id: str
    new_label: str
    existing_label: str
    risk: str

    def __bool__(self) -> bool:
        return bool(self.pair_id)


class ClassPairTable:
    def __init__(self, pairs: Iterable[ClassPair]) -> None:
        self._pairs = tuple(pairs)

    def __len__(self) -> int:
        return len(self._pairs)

    @property
    def is_empty(self) -> bool:
        return not self._pairs

    @classmethod
    def load_from_path(cls, path: str = DEFAULT_CLASS_PAIRS_PATH) -> "ClassPairTable":
        """讀配對表。讀不到或格式不符時回空表，不拋錯——與整條安全偵測路徑
        對主流程 fail-open 的方向一致。"""
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("類別配對表載入失敗，本次不偵測：%s", type(exc).__name__)
            return cls(())
        pairs = []
        for item in payload.get("pairs") or []:
            try:
                pairs.append(
                    ClassPair(
                        pair_id=item["id"],
                        a=ClassSide(item["a"]["label_zh"], tuple(item["a"]["prefixes"])),
                        b=ClassSide(item["b"]["label_zh"], tuple(item["b"]["prefixes"])),
                        risk_zh=item.get("risk_zh", ""),
                    )
                )
            except (KeyError, TypeError) as exc:
                logger.warning("類別配對格式不符，略過該筆：%s", type(exc).__name__)
        return cls(pairs)


def find_class_pair(
    new_atc_codes: Iterable[str],
    existing_atc_codes: Iterable[str],
    table: ClassPairTable,
) -> Optional[ClassPairFinding]:
    """新加入的藥與現有用藥之間，有沒有成立的類別配對。

    **兩個方向都要看**：新藥可能落在 a 側（買了止痛藥、正在吃抗凝血劑），
    也可能落在 b 側。只查一個方向會漏掉一半。

    表內順序即優先序，第一組成立就回傳——理由與 `_first_overlap` 相同：
    一則訊息塞進三組，長輩讀不完也分不清該問哪一個。
    """
    if table.is_empty:
        return None

    new_codes = list(new_atc_codes)
    existing_codes = list(existing_atc_codes)
    if not new_codes or not existing_codes:
        return None

    for pair in table._pairs:
        if pair.a.matches(new_codes) and pair.b.matches(existing_codes):
            return ClassPairFinding(
                pair.pair_id, pair.a.label_zh, pair.b.label_zh, pair.risk_zh
            )
        # 同類配自己時反向檢查是多餘的，跳過以免產生 a/b 標籤顛倒的重複結果。
        if pair.is_self_pair:
            continue
        if pair.b.matches(new_codes) and pair.a.matches(existing_codes):
            return ClassPairFinding(
                pair.pair_id, pair.b.label_zh, pair.a.label_zh, pair.risk_zh
            )
    return None
