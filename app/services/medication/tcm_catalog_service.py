"""中藥庫比對。

存在的理由與 `DrugCatalogService` 相同——把藥袋上的字串對應到一個已知的品項
——但兩者的資料性質差很多，因此比對規則也不同，不共用實作。

## 只用「方名」辨識，不用藥材名

實測（`resources/tcm_catalog.json` 對 `resources/drug_catalog.json`）：

- 200 個基準方名最短 3 字，而西藥品名裡含基準方名的只有 **2 筆**，且那兩筆
  （桂枝湯浸膏、止嗽散）本身就是中藥製劑。用方名辨識幾乎不會誤中西藥。
- 477 種藥材名裡有 **224 種只有 1～2 字**，拿去比對西藥品名撞出 **565 筆**
  （「人參」撞到人參萃取粉劑、人參精軟膠囊…）。**藥材名不能用來辨識**。

因此藥材名只在辨識成功之後，作為交互作用查詢的鍵使用（見
`app/services/safety/tcm_interaction.py`）——那時候已經知道這是哪一帖藥，
不再有身分誤判的問題。

## 含容比對取最長命中

藥袋印的常是商品名（「順天堂葛根湯濃縮顆粒」），方名只是其中一段，因此要做
含容比對。而 200 個方名裡有 9 對互為子字串（四物湯 ⊂ 桃紅四物湯、葛根湯 ⊂
升麻葛根湯、逍遙散 ⊂ 加味逍遙散…），所以命中多個時取**最長的那個**：藥袋
寫「桃紅四物湯」就是桃紅四物湯，不是四物湯。取最短或取第一個都會把加味方
誤判成基準方，而兩者的組成不同。

## 不比對濃縮製劑條目

藥典的 9 筆「濃縮製劑」（小青龍湯濃縮製劑…）與 200 基準方重複，但它們的
`herbs` 是自己的名字而不是組成藥材——拿它們辨識會得到一個查不到任何交互
作用的鍵。辨識一律只看 `kind == "formula"`。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from app.services.safety.tcm_interaction import normalize_tcm_name

logger = logging.getLogger(__name__)

DEFAULT_TCM_CATALOG_PATH = "resources/tcm_catalog.json"

# 可用於辨識的最短方名長度。實測基準方名最短就是 3 字，這裡設同一個值是
# 為了讓「規則」與「資料現況」明確對齊——若日後資料出現更短的方名，它會被
# 擋下來而不是悄悄開始誤中。
_MIN_FORMULA_LENGTH = 3


@dataclass(frozen=True)
class TcmCatalogEntry:
    """一帖基準方。

    `herbs` 是組成藥材，已去掉份量——比對要問的是「這一方含不含麻黃」，
    不是含多少（見 `scripts/build_tcm_catalog.parse_herbs`）。
    """

    code: str
    name_zh: str
    herbs: tuple[str, ...] = ()
    source: str = ""
    effect: str = ""
    # 適應症會直接揭露病情，與 `Medication.indication` 同一條慣例：
    # 僅供 LIFF 內呈現，SHALL NOT 進入任何推播訊息。
    indication: str = ""

    @property
    def interaction_keys(self) -> tuple[str, ...]:
        """查交互作用時要用的鍵，**方名在最前面**。

        配對表兩種層級都有（「葛根湯 × Aspirin」是方、「甘草 × 利尿劑」是
        藥材），兩種都要查。順序有意義：使用者吃的是「葛根湯」，訊息講方名
        他看得懂，講「葛根」他不知道那是哪一包，見
        `find_tcm_interaction` 的說明。
        """
        return (self.name_zh, *self.herbs)


class TcmCatalogService:
    """把藥袋上的字串對應到基準方。

    建構子接受已載入的條目而非路徑，比照 `DrugCatalogService`：測試才能直接
    餵小型固定資料集，不必碰檔案系統。
    """

    def __init__(self, entries: Iterable[TcmCatalogEntry]) -> None:
        self._by_key: dict[str, TcmCatalogEntry] = {}
        for entry in entries:
            key = normalize_tcm_name(entry.name_zh)
            if len(key) < _MIN_FORMULA_LENGTH:
                continue
            self._by_key.setdefault(key, entry)
        # 長的先比，讓含容比對天然取到最長命中。
        self._keys_by_length = sorted(self._by_key, key=len, reverse=True)

    def __len__(self) -> int:
        return len(self._by_key)

    @property
    def is_empty(self) -> bool:
        return not self._by_key

    @classmethod
    def load_from_path(
        cls, path: str = DEFAULT_TCM_CATALOG_PATH
    ) -> "TcmCatalogService":
        """讀中藥庫。讀不到或格式不符時回空庫，不拋錯。

        空庫的效果是「所有藥名都不會被認成中藥」——退化方向是少偵測，
        不是誤判，與整條安全偵測路徑既有的方向一致。
        """
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("中藥庫載入失敗，本次不辨識中藥：%s", type(exc).__name__)
            return cls([])
        entries = [
            TcmCatalogEntry(
                code=item.get("code", ""),
                name_zh=item.get("name_zh", ""),
                herbs=tuple(item.get("herbs") or ()),
                source=item.get("source", ""),
                effect=item.get("effect", ""),
                indication=item.get("indication", ""),
            )
            for item in payload
            if item.get("kind") == "formula" and item.get("name_zh")
        ]
        if not entries:
            logger.warning("中藥庫條目數為 0，本次不辨識中藥")
        return cls(entries)

    def match(self, name: str) -> Optional[TcmCatalogEntry]:
        """藥袋上的字串是不是某一帖基準方。

        先完全比對，再含容比對取最長命中。查無回 None——「認不出來」與
        「認出是中藥」必須分得開，呼叫端據此決定要不要走中藥路徑。
        """
        key = normalize_tcm_name(name)
        if not key or not self._by_key:
            return None
        exact = self._by_key.get(key)
        if exact is not None:
            return exact
        for candidate in self._keys_by_length:
            if candidate in key:
                return self._by_key[candidate]
        return None
