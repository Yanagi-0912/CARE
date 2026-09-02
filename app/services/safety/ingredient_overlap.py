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
    def load_from_path(cls, path: str = DEFAULT_WATCHLIST_PATH) -> "IngredientWatchlist":
        """讀白名單檔。讀不到或格式不符時回空清單，不拋錯。

        空清單的效果是「不偵測任何重複」——與整條路徑對主流程 fail-open 的
        方向一致：使用者並沒有在等這個結果，一則「偵測失敗」只會造成困惑。
        """
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            entries = payload.get("ingredients") or []
            return cls(entry.get("name", "") for entry in entries)
        except Exception as exc:  # noqa: BLE001
            # log 不帶路徑以外的內容：白名單本身不含個資，但保持與本模組
            # 其他 log 一致的克制。
            logger.warning("成分白名單載入失敗，本次不偵測重複：%s", type(exc).__name__)
            return cls([])


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
