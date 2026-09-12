"""中藥與西藥的交互作用偵測。

純函式與純資料，不做 I/O、不決定要通知誰——比照 `risk_rules`、
`ingredient_overlap` 既有的分工：判定門檻留在這裡，副作用留在呼叫端。

## 為什麼不能用既有的成分比對

`find_overlap` 與 `find_class_stacking` 都是字串相等比對，而中藥材是中文名
（麻黃、甘草），西藥成分是英文學名（DL-METHYLEPHEDRINE HCL、FUROSEMIDE）。
麻黃與麻黃鹼在藥理上是同一件事，在字串上完全無關，兩邊永遠交集不到。

要橋接就需要一張「中藥名 × 西藥名」的對照表，而那是藥理知識，不能由我們推測。
本模組讀的正是衛福部自己維護的那張表（見
`scripts/build_tcm_interactions.py`）——每一組配對都有官方出處，不是我們猜的。

## 西藥側為什麼可以用詞界前綴，而抗膽鹼清單不行

`ingredient_overlap.IngredientClass` 刻意用完全比對，因為實測子字串比對會誤中
（`CLIDINIUM` 撈到吸入型 LAMA 的 `ACLIDINIUM`、`SCOPOLAMINE` 撈到四級銨的
`BUTYLSCOPOLAMINE`）——危險來自**字串中間**的包含。

這裡用的是**詞界前綴**（名稱後必須接一個空白），性質不同：藥證庫的成分字串
結構是「主成分 ＋ 鹽類或規格修飾」，因此 `X ` 開頭的字串必然是 X 的某個型式。
實測配對表的 128 個西藥名對全庫 7,045 個成分字串，詞界前綴多出來的 110 組
命中逐一檢視，全部是同一成分的修飾（`ACETAMINOPHEN CRYSTAL`、`ASPIRIN
ALUMINUM`、`AMLODIPINE BESILATE`），沒有一組是別的藥。

覆蓋率：配對表 128 個西藥名裡 107 個對得上藥證庫（84%），1,841 組具名配對裡
1,636 組可用（89%）。對不上的多是未在台上市或以商品名登錄者（AGGRENOX、
BAKTAR (TMP+SMX)）。

## 類別列不參與比對

配對表另有 35 組的西藥側是中文藥理類別（抗凝血劑、NSAID類、降血壓藥…），
對不上任何成分字串。它們留在檔案裡但**不進入比對**——要用得先以 ATC 碼建立
類別對應（`drug_catalog.json` 現已有 `atc_codes`），那是另一個決定，在此之前
悄悄用中文類別名去比對成分只會全部落空，還讓人以為已經涵蓋了。
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

DEFAULT_INTERACTIONS_PATH = "resources/tcm_drug_interactions.json"


def normalize_tcm_name(name: str) -> str:
    """中藥名的比對鍵。

    NFKC 是必要的，不是防禦性的：實測衛福部交互作用資料庫的「小青龍湯」用的是
    CJK 相容表意字 U+F9C4（龍），而中藥庫用的是常用的 U+9F8D——**肉眼完全一樣，
    字串不相等**。沒有這一步，含麻黃的關鍵方劑之一會靜默地永遠比不到。

    另外去掉劑型標記與括號補述（《丸》《散》、（顆粒、散）），讓「六味地黃丸
    《丸》」與「六味地黃丸」落在同一個鍵；以及藥典品項的「【飲片】」前綴。
    """
    text = unicodedata.normalize("NFKC", name or "")
    text = re.sub(r"^【[^】]*】", "", text)
    text = re.sub(r"《[^》]*》", "", text)
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    return re.sub(r"\s+", "", text).strip()


@dataclass(frozen=True)
class TcmInteractionFinding:
    """一組成立的中西藥交互作用。

    `summary` 原樣帶自來源的機制摘要（常是「機制未明。」）。**它不得直接進入
    推播**：來源站自述「僅供藥師參考，不宜使用在疾病判斷及治療」，而長輩讀
    「機制未明」只會困惑。呈現面該做的是導向藥師。
    """

    tcm_name: str
    western_ingredient: str
    summary: str = ""

    def __bool__(self) -> bool:
        return bool(self.tcm_name and self.western_ingredient)


class TcmInteractionTable:
    """中藥名 → 可能交互作用的西藥成分。

    只收 `western_kind == "ingredient"` 的配對；類別列不進來，理由見模組文件。
    """

    def __init__(self, pairs: Iterable[dict]) -> None:
        self._by_tcm: dict[str, list[tuple[str, str]]] = {}
        for pair in pairs:
            if pair.get("western_kind") != "ingredient":
                continue
            tcm = normalize_tcm_name(pair.get("tcm", ""))
            western = (pair.get("western") or "").strip().upper()
            if not tcm or not western:
                continue
            self._by_tcm.setdefault(tcm, []).append((western, pair.get("summary", "")))
        # 排序讓同一組輸入永遠產生同一則訊息——訊息會進推播，順序不穩定會讓
        # 相同狀況看起來像不同事件，比照 `find_overlap`。
        for entries in self._by_tcm.values():
            entries.sort()

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_tcm.values())

    @property
    def is_empty(self) -> bool:
        return not self._by_tcm

    @classmethod
    def load_from_path(
        cls, path: str = DEFAULT_INTERACTIONS_PATH
    ) -> "TcmInteractionTable":
        """讀配對表。讀不到或格式不符時回空表，不拋錯。

        空表的效果是「不偵測任何中西藥交互作用」——與整條安全偵測路徑對主流程
        fail-open 的方向一致，比照 `IngredientWatchlist.load_from_path`。
        """
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(payload.get("pairs") or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "中西藥交互作用表載入失敗，本次不偵測：%s", type(exc).__name__
            )
            return cls([])


def western_ingredient_matches(pair_name: str, catalog_ingredient: str) -> bool:
    """藥證庫的成分字串是不是配對表那個西藥。

    完全相等，或以「配對表名 ＋ 一個空白」開頭——後者涵蓋鹽類與規格修飾
    （`AMIODARONE` → `AMIODARONE HCL`、`ZOLPIDEM` → `ZOLPIDEM TARTRATE`）。

    **必須是詞界，不能是任意前綴**：沒有那個空白，`NIACIN` 會命中
    `NIACINAMIDE`，那是兩種不同的東西。
    """
    name = (pair_name or "").strip().upper()
    ingredient = (catalog_ingredient or "").strip().upper()
    if not name or not ingredient:
        return False
    return ingredient == name or ingredient.startswith(name + " ")


def find_tcm_interaction(
    tcm_keys: Iterable[str],
    western_ingredients: Iterable[str],
    table: TcmInteractionTable,
) -> Optional[TcmInteractionFinding]:
    """這帖中藥與這些西藥成分之間，有沒有登錄過的交互作用。

    `tcm_keys` 應同時包含方名與其組成藥材：配對表兩種都有（「葛根湯 × Aspirin」
    是方層級，「甘草 × 利尿劑」是藥材層級），只給其中一種會漏掉另一半。

    **鍵的順序由呼叫端決定，這裡不排序。** 呼叫端應把方名放在最前面：使用者
    吃的是「葛根湯」，訊息講「葛根湯和阿斯匹靈」他看得懂，講「葛根和阿斯匹靈」
    他不知道那是哪一包。排序會把藥材排到方名前面，正好破壞這件事。

    只回報第一組，不是全部——理由與 `_first_overlap` 相同：一則訊息塞進三組，
    長輩讀不完也分不清該問哪一個，而只要有任何一組成立，該做的事都一樣。

    決定性由「呼叫端順序 ＋ 表內排序」共同保證：同一組輸入永遠產生同一則訊息。
    """
    if table.is_empty:
        return None

    keys = list(
        dict.fromkeys(
            normalize_tcm_name(k) for k in tcm_keys if k and k.strip()
        )
    )
    keys = [k for k in keys if k]
    ingredients = [i for i in western_ingredients if i and i.strip()]
    if not keys or not ingredients:
        return None

    for key in keys:
        for western, summary in table._by_tcm.get(key, ()):
            for ingredient in ingredients:
                if western_ingredient_matches(western, ingredient):
                    return TcmInteractionFinding(
                        tcm_name=key, western_ingredient=western, summary=summary
                    )
    return None
