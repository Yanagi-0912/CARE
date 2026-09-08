"""仿單適應症的查詢與比對。

存在的理由有兩個，彼此獨立：

1. **呈現**：讓 LIFF 能在藥袋讀出的適應症之外，補上食藥署仿單的說法。兩者
   回答的是不同問題——仿單答「這個藥核准用於哪些適應症」（監管範疇），藥袋
   上印的通常是醫師針對這位病人挑過的那一個。因此是並陳，不是取代。
2. **核對線索**：藥袋讀出的適應症若與仿單完全不相干，是模型讀錯藥名的訊號。
   但**這個結果只記錄，不影響信心度**——理由見 `compare` 的說明。

刻意與 `DrugCatalogService` 分開：那支的職責是藥名比對，其字元 n-gram 反向
索引是效能敏感結構（當初正是為了修「模糊比對未命中時卡住事件迴圈
400~750ms」而建）。適應症對藥名比對毫無貢獻，併入只會讓它與常駐記憶體無謂
變大（實測 15.9 MB → 22.2 MB）。見
`openspec/changes/drug-indication/design.md` 決策 1。

建構子接受已載入的條目而非路徑，測試才能直接餵小型固定資料集，不必碰檔案
系統，也不必 monkey patch——與 `DrugCatalogService` 同一慣例。
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Iterable, Literal, Optional

logger = logging.getLogger(__name__)

# 比對結果。`unchecked` 與 `unrelated` 必須分得開：前者是「沒有依據可比」，
# 後者是「比過了而且對不上」。把缺資料當成證據不利，等於讓沒印適應症的藥袋
# （適應症不是法定必載欄位）全部被標記。
IndicationMatch = Literal["unchecked", "consistent", "unrelated"]

# 比對用的字元 n-gram 大小。中文醫學名詞的資訊密度高，2 字已有足夠鑑別度；
# 取 3 會讓「胃炎」「便祕」這種兩字詞連一個 gram 都湊不出來。
_GRAM_SIZE = 2

_NON_CJK = re.compile(r"[^一-鿿]")

# 適應症敘述裡到處都是、對「這兩段在講不在講同一件事」毫無鑑別度的字。
# 不排除的話，「治療」「症狀」這類字會讓任意兩段適應症都有重疊，比對規則
# 形同虛設。
#
# 這份清單是依實測資料手寫的，完整性未經檢驗——可能仍有無鑑別度的字漏收
# （會抬高誤判率）或誤收有鑑別度的字（會抬高漏抓率）。列為
# design.md 的 Open Question，待有真實藥袋樣本後校準。
_STOPWORDS = set(
    "之及與或等引起使用治療適用本品病人患者症狀改善緩解預防輔助"
    "以上下的和有為所可能相關各種其他一二三四五六七八九十"
)


@dataclass(frozen=True)
class DrugIndication:
    """單一藥證的仿單適應症。

    `summary` 為空字串代表「不需要摘要」或「產不出合格摘要」——兩者對呈現面
    的意義相同（都退回顯示 `text`），刻意不分成兩種狀態徒增判斷。
    """

    text: str
    summary: str = ""

    @property
    def display_text(self) -> str:
        """呈現面該顯示的那一段：有合格摘要就用摘要，否則退回原文。"""
        return self.summary or self.text


def _grams(text: str) -> set[str]:
    """把一段適應症切成可比對的字元 n-gram 集合。

    只保留中日韓統一表意文字：英文藥學名詞與菌株學名（Staphylococcus 之類）
    在藥袋上幾乎不會出現，留著只會讓仿單那側多出一堆永遠配不到的 gram。
    """
    cleaned = _NON_CJK.sub("", text or "")
    cleaned = "".join(ch for ch in cleaned if ch not in _STOPWORDS)
    if len(cleaned) < _GRAM_SIZE:
        return set()
    return {cleaned[i : i + _GRAM_SIZE] for i in range(len(cleaned) - _GRAM_SIZE + 1)}


class DrugIndicationService:
    def __init__(self, entries: Optional[dict] = None):
        self._by_license: dict[str, DrugIndication] = {}
        for license_number, raw in (entries or {}).items():
            if not license_number or not isinstance(raw, dict):
                continue
            text = (raw.get("text") or "").strip()
            if not text:
                continue
            self._by_license[license_number] = DrugIndication(
                text=text,
                summary=(raw.get("summary") or "").strip(),
            )

    @property
    def is_empty(self) -> bool:
        return not self._by_license

    @classmethod
    def load_from_path(cls, path: str) -> "DrugIndicationService":
        """從靜態檔載入。

        檔案缺席或損毀時回傳空服務而非拋錯：仿單適應症不是啟動的必要條件，
        缺席的後果是查無仿單（呈現面只顯示藥袋那行、比對一律 unchecked），
        這個退化方向是安全的，讓應用起不來則不是——與
        `DrugCatalogService.load_from_path` 同一種降級。
        """
        try:
            with open(path, "r", encoding="utf-8") as indication_file:
                raw = json.load(indication_file)
            if not isinstance(raw, dict):
                raise ValueError("產出物不是以證號為鍵的物件")
            service = cls(raw)
            if service.is_empty:
                logger.warning(
                    "仿單適應症載入完成但條目數為 0：%s；"
                    "LIFF 將只顯示藥袋讀到的適應症，比對一律為 unchecked",
                    path,
                )
            else:
                logger.info(
                    "仿單適應症載入完成：%s，共 %d 筆", path, len(service._by_license)
                )
            return service
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            logger.error(
                "仿單適應症載入失敗，LIFF 將只顯示藥袋讀到的適應症：%s（%s）",
                path,
                exc,
            )
            return cls({})

    def lookup(self, license_number: Optional[str]) -> Optional[DrugIndication]:
        """查一張藥證的仿單適應症。證號未確定（None／空字串）時一律回 None。

        呼叫端據此決定要不要顯示仿單區塊——這與 `drug-appearance` 的「證號
        不確定時不得顯示藥丸照片」是同一條安全邊界：不知道是哪一張藥證，
        顯示的內容就可能屬於另一顆藥。
        """
        if not license_number:
            return None
        return self._by_license.get(license_number)

    def compare(
        self, bag_indication: Optional[str], license_number: Optional[str]
    ) -> IndicationMatch:
        """比對藥袋讀出的適應症與該藥證的仿單。

        回傳 `unchecked` 的三種情況——藥袋沒印適應症、證號未確定、查無該藥證
        的仿單——都是「沒有依據可比」，SHALL NOT 記為 `unrelated`。適應症不是
        藥袋的法定必載欄位（衛署藥字第0910033863號公告的必載項目不含它），
        缺席是常態而非異常。

        **呼叫端 SHALL NOT 讓這個結果影響信心度分級。** 本規則的誤判率尚未
        以真實藥袋量測過：以「藥袋短語對仿單長文」模擬，誤判率落在 17%~25%，
        而信心度要求全部藥品皆通過，一顆誤判就會讓整份草稿失去一鍵確認
        （三種藥的藥袋維持高信心的機率僅約 51%）。見
        `openspec/changes/drug-indication/specs/drug-indication/spec.md`
        的「比對結果不得影響信心度」。
        """
        bag_text = (bag_indication or "").strip()
        if not bag_text:
            return "unchecked"
        indication = self.lookup(license_number)
        if indication is None:
            return "unchecked"

        bag_grams = _grams(bag_text)
        spc_grams = _grams(indication.text)
        if not bag_grams or not spc_grams:
            # 任一側去除停用字後湊不出一個 gram，等於沒有可比的內容。
            return "unchecked"
        return "consistent" if bag_grams & spc_grams else "unrelated"
