"""非處方藥的成分重複偵測。

純函式，不做 I/O、不查資料庫、不決定要通知誰——判定門檻留在這裡，副作用留在
呼叫端，比照 `risk_rules` 與 `SafetyAlertService` 既有的分工。

為什麼需要這個：長輩認為「感冒藥」與「止痛藥」是兩種不同的東西，因此可以一起
吃。但實測非處方藥 15,191 種裡有 1,739 種含乙醯胺酚（11.4%），而它們的外觀
完全看不出來——「安痛錠」只有一種成分還算看得出來，「鼻寧通膠囊」裡藏著乙醯
胺酚就完全看不出來了。成分重複是台灣最常見的成藥意外。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

DEFAULT_WATCHLIST_PATH = "resources/otc_watch_ingredients.json"

# 會觸發偵測的藥品分級。處方藥不在其中——它已經過醫師診斷與藥師調劑，
# 再警示一次只是噪音，而通知量該與風險成正比。
OTC_DRUG_CLASSES = frozenset({"otc", "otc_guided"})


@dataclass(frozen=True)
class OverlapFinding:
    """一次偵測的結果。`ingredients` 為空代表沒有值得通知的重複。"""

    ingredients: tuple[str, ...]

    def __bool__(self) -> bool:
        return bool(self.ingredients)


class IngredientWatchlist:
    """監測成分白名單。

    為什麼是白名單而不是比對全部成分：實測非處方藥最常見的 20 種成分裡有一半
    是維生素。兩種綜合感冒藥都含維生素 B2 是常態，報出來沒有臨床意義，而全成分
    比對會讓警報變成背景雜訊、淹掉真正該看的那一則。這個功能的價值完全建立在
    「發出來的每一則都值得看」。
    """

    def __init__(self, names: Iterable[str]) -> None:
        self._names = frozenset(n.strip().upper() for n in names if n and n.strip())

    def __contains__(self, ingredient: str) -> bool:
        return (ingredient or "").strip().upper() in self._names

    def __len__(self) -> int:
        return len(self._names)

    @property
    def is_empty(self) -> bool:
        return not self._names

    @classmethod
    def _load_payload(cls, path: str) -> dict:
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("成分白名單載入失敗，本次不偵測重複：%s", type(exc).__name__)
            return {}

    @classmethod
    def load_from_path(cls, path: str = DEFAULT_WATCHLIST_PATH) -> "IngredientWatchlist":
        """讀白名單檔。讀不到或格式不符時回空清單，不拋錯。

        空清單的效果是「不偵測任何重複」——與整條路徑對主流程 fail-open 的
        方向一致：使用者並沒有在等這個結果，一則「偵測失敗」只會造成困惑。
        """
        try:
            entries = cls._load_payload(path).get("ingredients") or []
            return cls(entry.get("name", "") for entry in entries)
        except Exception as exc:  # noqa: BLE001
            logger.warning("成分白名單解析失敗，本次不偵測重複：%s", type(exc).__name__)
            return cls([])


def load_local_action_forms(path: str = DEFAULT_WATCHLIST_PATH) -> frozenset:
    """不參與比對的劑型（局部作用、全身吸收量可忽略）。

    實測隨機配對時出現「眼藥水 + 止咳糖漿」同時含 CHLORPHENIRAMINE MALEATE
    而被判為重複——眼藥水是局部使用，這種重複沒有臨床意義，報出來會侵蝕
    「每一則都值得看」的前提。

    清單只列毫無疑義的局部用藥，**未列出的劑型一律照常比對**。外用不等於不
    吸收：穿皮貼片與栓劑都是刻意設計成全身吸收的，軟膏的水楊酸類也有經皮
    吸收累加風險。寧可多報一則局部用藥的重複，也不要因為一條猜出來的規則
    漏掉真正的過量風險。
    """
    payload = IngredientWatchlist._load_payload(path)
    forms = (payload.get("local_action_dosage_forms") or {}).get("forms") or []
    return frozenset(f.strip() for f in forms if f and f.strip())


def is_local_action(dosage_form: Optional[str], local_forms: frozenset) -> bool:
    """這個劑型是否為局部作用。認不得的劑型回 False（照常比對）。"""
    return (dosage_form or "").strip() in local_forms


def find_overlap(
    new_ingredients: Iterable[str],
    existing_ingredients: Iterable[str],
    watchlist: IngredientWatchlist,
) -> OverlapFinding:
    """新藥與現有用藥之間，有哪些白名單成分重複。

    兩邊的成分都應該已經由 `build_drug_catalog.normalize_ingredient` 正規化過
    （去括號補述、大寫、收斂空白）。這裡再做一次 upper/strip 是防禦性的：
    呼叫端可能直接餵使用者輸入或舊版藥證庫的資料。

    回傳的成分依字母排序，讓同一組輸入永遠產生同一則訊息——訊息內容進得了
    推播，順序不穩定會讓相同狀況看起來像不同事件。
    """
    if watchlist.is_empty:
        return OverlapFinding(())

    new_set = {i.strip().upper() for i in new_ingredients if i and i.strip()}
    existing_set = {i.strip().upper() for i in existing_ingredients if i and i.strip()}
    shared = new_set & existing_set
    watched = sorted(i for i in shared if i in watchlist)
    return OverlapFinding(tuple(watched))


def should_check(drug_class: Optional[str]) -> bool:
    """這個分級要不要做重複偵測。

    未知分級（空字串）一律不檢查：`classify_drug` 對認不得的類別回空字串而
    不猜，這裡承接同一個保守方向——寧可少偵測，不要對一個我們不知道是什麼的
    東西發警報。
    """
    return (drug_class or "") in OTC_DRUG_CLASSES


# ---- 同類累加（抗膽鹼疊加）--------------------------------------------------
#
# 與上面的「成分重複」是兩條互補的規則，形狀相同、判準不同：
#
#   成分重複：A 和 B 含**同一個**成分，且該成分在 IngredientWatchlist 上。
#   同類累加：A 和 B 含**不同**成分，但兩者都在同一份藥理類別清單上。
#
# 為什麼需要後者：實測藥證庫，含第一代抗組織胺的非處方藥有 3,099 種，其中
# 2,112 種含 CHLORPHENIRAMINE。也就是說「感冒藥 + 感冒藥」這種同成分疊加，
# 現有規則已經抓得到；抓不到的是「感冒藥（CHLORPHENIRAMINE）+ 暈車藥
# （DIMENHYDRINATE）」——成分名稱不同，交集是空的，但藥理上是同一件事：
# 抗膽鹼作用加倍，對長者的後果是頭暈、意識混亂，終點是跌倒與骨折。
#
# 依據：2023 AGS Beers Criteria Table 5「抗膽鹼藥 × 抗膽鹼藥」（建議強度：強）；
# PIM-Taiwan 2018 Table 1。清單與逐項出處見
# `resources/anticholinergic_ingredients.json`。

DEFAULT_ANTICHOLINERGIC_PATH = "resources/anticholinergic_ingredients.json"


@dataclass(frozen=True)
class StackingFinding:
    """一次同類累加的偵測結果。

    兩個成分分開存放而不是一個集合：訊息要講的是「這個新藥的這個成分，和你
    正在吃的那個藥的那個成分」，兩側各自屬於哪個藥是有意義的，攤平成集合就
    講不出這句話了。
    """

    new_ingredient: str
    existing_ingredient: str

    def __bool__(self) -> bool:
        return bool(self.new_ingredient and self.existing_ingredient)


class IngredientClass:
    """單一藥理類別的成分清單。

    比對語意刻意與 `IngredientWatchlist` 相同——**完全比對，不是子字串比對**。
    實測子字串比對會誤中三組有臨床意義的成分：`CLIDINIUM` 撈到吸入型 LAMA
    的 ACLIDINIUM／UMECLIDINIUM，`SCOPOLAMINE` 撈到四級銨的 BUTYLSCOPOLAMINE
    ——後者不通過血腦障壁，正因如此才被選用，拿它當「認知衰退與跌倒」的證據
    是錯的。因此清單逐一列舉藥證庫裡實際出現的成分字串（含鹽類與異構物）。
    """

    def __init__(self, names: Iterable[str]) -> None:
        self._names = frozenset(n.strip().upper() for n in names if n and n.strip())

    def __contains__(self, ingredient: str) -> bool:
        return (ingredient or "").strip().upper() in self._names

    def __len__(self) -> int:
        return len(self._names)

    @property
    def is_empty(self) -> bool:
        return not self._names

    @classmethod
    def load_from_path(
        cls, path: str = DEFAULT_ANTICHOLINERGIC_PATH
    ) -> "IngredientClass":
        """讀清單檔。讀不到或格式不符時回空清單，不拋錯。

        空清單的效果是「不偵測任何累加」，與整條路徑對主流程 fail-open 的方向
        一致，比照 `IngredientWatchlist.load_from_path`。
        """
        try:
            entries = IngredientWatchlist._load_payload(path).get("ingredients") or []
            return cls(entry.get("name", "") for entry in entries)
        except Exception as exc:  # noqa: BLE001
            logger.warning("抗膽鹼清單解析失敗，本次不偵測累加：%s", type(exc).__name__)
            return cls([])


def find_class_stacking(
    new_ingredients: Iterable[str],
    existing_ingredients: Iterable[str],
    ingredient_class: IngredientClass,
) -> Optional[StackingFinding]:
    """兩個藥是否各自含有同一類別的**不同**成分。

    相同成分一律不回報，即使兩邊都命中類別清單——那個情況已經由
    `find_overlap` 涵蓋（CHLORPHENIRAMINE 同時在兩份清單上），在這裡再回報
    一次會讓同一個狀況產生兩則訊息，而使用者要做的事完全一樣。這條規則存在
    的理由就是補上「成分不同」的那一半，不是重疊它。

    回傳的配對取字典序最小的一組，讓同一組輸入永遠產生同一則訊息——訊息會進
    推播，順序不穩定會讓相同狀況看起來像不同事件，比照 `find_overlap`。
    """
    if ingredient_class.is_empty:
        return None

    new_hits = {
        i.strip().upper()
        for i in new_ingredients
        if i and i.strip() and i.strip().upper() in ingredient_class
    }
    existing_hits = {
        i.strip().upper()
        for i in existing_ingredients
        if i and i.strip() and i.strip().upper() in ingredient_class
    }
    if not new_hits or not existing_hits:
        return None

    pairs = sorted(
        (n, e) for n in new_hits for e in existing_hits if n != e
    )
    if not pairs:
        return None
    return StackingFinding(new_ingredient=pairs[0][0], existing_ingredient=pairs[0][1])
