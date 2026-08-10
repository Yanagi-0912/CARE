"""藥證庫比對。

存在的理由只有一個：偵測視覺模型的錯讀。模型把「脈優錠」讀成形近的其他藥名時，
它自述的信心度仍然很高，唯一能發現該字串不對應任何一張核准藥證的方法，
就是拿去跟外部字典比對。比對不到即降為低信心，強制人工核對。

建構子接受已載入的條目而非路徑，測試才能直接餵小型固定資料集，
不必碰檔案系統，也不必 monkey patch。

比對分三個階段（依序，任何一階段命中就不再往下）：

1. 完全比對：正規化後字串相同，score 1.0。
2. 含容比對：藥袋通常只印短的品牌名（「普拿疼」），藥證上卻是連劑型、
   劑量都在內的全名（「普拿疼錠500毫克」）。單純用 SequenceMatcher 比
   長度差這麼大的兩個字串，比值會被長度差拉低到任何門檻都分不開「這是
   同一顆藥的縮寫」跟「這是兩個不相干的字串」（實測：'普拿疼' 對
   '普拿疼錠500毫克' 只有 0.500）。含容比對繞過這個問題：直接檢查其中
   一個字串是不是另一個的子字串。命中不只一張藥證時（「普拿疼」同時是
   好幾個普拿疼系列產品品名的子字串），藥名本身已被驗證為真實存在的
   核准藥品，但無法判斷是哪一個品項——見 DrugCatalogMatch 的說明。
3. 模糊比對：對前兩階段都沒處理到的查詢做 SequenceMatcher，取門檻以上
   的最高分。

第 2、3 階段的候選集合都來自同一份字元 n-gram 反向索引（建構子裡建一次），
不再對全部鍵做線性掃描——這是修正「模糊比對未命中時卡住事件迴圈
400~750ms」缺陷的根本作法，細節見各方法的說明。
"""

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# 藥證品名常見的廠商前綴：'"福元"蘇打錠500毫克'。藥袋上通常只印藥名，
# 不會帶這段，所以正規化時要拿掉。全形與半形引號都要涵蓋。
_LEADING_MANUFACTURER = re.compile(r'^\s*[「『"“”‘’](.*?)[」』"“”‘’]\s*')
_QUOTE_CHARS = re.compile(r'[「」『』"“”‘’\']')
_WHITESPACE = re.compile(r"\s+")

# 字元 n-gram 的大小。中文藥名的資訊密度高，2 字已有足夠鑑別度；
# 選太大會讓短於這個長度的查詢字串連一個 gram 都湊不出來。
_GRAM_SIZE = 2

# 含容比對的最小查詢長度。低於這個長度時，任何字串幾乎都會是某個藥證
# 品名的子字串（例如單一個「錠」字），含容比對會失去意義、變成幾乎
# 全部命中，所以短於此長度一律跳過含容比對，直接落到模糊比對。
_MIN_CONTAINMENT_LENGTH = 3

# 候選集合聯集「查詢裡最罕見的一個 gram」時，該 gram 的 postings 長度
# 上限。常見 gram（英文藥名裡到處都是的 'ER'、'TA'，中文的「膜衣」
# 「錠」）postings 可能長達數萬筆，若不設上限，聯集等於又把全庫掃了
# 一遍，索引就白建了。超過上限就不聯集這個 gram——含容比對用的是
# 交集（見 `_candidates` 的說明），不依賴這一步也能找到主要方向的
# 候選；這一步只是額外補反方向與模糊比對的召回率。
_MAX_RARE_GRAM_POSTINGS = 3000


@dataclass(frozen=True)
class DrugCatalogEntry:
    license_number: str
    name_zh: str
    name_en: str = ""


@dataclass(frozen=True)
class DrugCatalogMatch:
    """藥名比對結果。

    `license_number` 是 Optional，這個 None 只代表一種情況：含容比對
    命中了不只一張藥證字號（例如「普拿疼」同時是好幾個普拿疼系列產品
    品名的子字串）。這種情況下藥名本身已經被驗證為真實存在、核准過的
    藥品——這正是本比對唯一的存在理由——只是不知道對應哪一個品項，
    因此不得任意選一個冒充答案（選第一個、選最短的都是編造），寧可
    留空也不要讓使用者的用藥提醒掛上一個他根本沒有被開立的藥證字號。

    呼叫端判斷「這個藥名有沒有通過藥證庫校驗」必須看 `match()` 的回傳
    值是不是 None，而不是看 `license_number` 是不是 None——後者只表示
    「知不知道是哪一張藥證」，是校驗結果之外的另一個維度，把兩者混在
    一起會讓含容命中但無法定位品項的藥名被誤判成未驗證，重新強制走
    人工核對，抵銷了含容比對原本要解決的問題。
    """

    license_number: Optional[str]
    name_zh: str
    name_en: str
    score: float


def normalize_drug_name(name: str) -> str:
    """把藥名收斂成可比對的鍵。

    全形轉半形、去引號與廠商前綴、去除空白、統一大小寫。這些差異在藥袋與
    藥證之間普遍存在，不正規化會讓大量正確的藥名比對不到。
    """
    if not name:
        return ""
    # NFKC 一併處理全形英數與全形空白
    normalized = unicodedata.normalize("NFKC", name)
    normalized = _LEADING_MANUFACTURER.sub("", normalized)
    normalized = _QUOTE_CHARS.sub("", normalized)
    normalized = _WHITESPACE.sub("", normalized)
    return normalized.upper()


def _ngrams(key: str, size: int = _GRAM_SIZE) -> set[str]:
    """把字串切成字元 n-gram 集合，供反向索引使用。

    長度不足一個 gram 的字串（正規化後只剩 1 個字，或空字串已在呼叫端
    擋掉）就把整個字串當成唯一的 gram——仍要能被索引到、被查到，不能
    因為太短就直接失去候選資格。
    """
    if not key:
        return set()
    if len(key) < size:
        return {key}
    return {key[i : i + size] for i in range(len(key) - size + 1)}


class DrugCatalogService:
    def __init__(self, entries: Iterable[DrugCatalogEntry], threshold: float):
        self._entries = list(entries)
        self._threshold = threshold
        # 正規化後的鍵 → 條目。同一條目的中英文品名各佔一個鍵。
        self._by_key: dict[str, DrugCatalogEntry] = {}
        for entry in self._entries:
            for raw_name in (entry.name_zh, entry.name_en):
                key = normalize_drug_name(raw_name)
                if key:
                    self._by_key.setdefault(key, entry)

        # gram → 含這個 gram 的鍵集合。在建構子裡建一次；之後每次查詢
        # 都只查這份索引取候選，不再對 `_by_key` 做線性掃描。真實藥證庫
        # 有 11 萬多個鍵，每次未命中的查詢都線性掃一遍 SequenceMatcher
        # 需要 400~750ms，且 `scan()` 是 async 路徑，會卡住整個行程
        # （含用藥提醒排程器）——這份索引就是用來擋掉那個代價。
        self._gram_index: dict[str, set[str]] = {}
        for key in self._by_key:
            for gram in _ngrams(key):
                self._gram_index.setdefault(gram, set()).add(key)

    @property
    def is_empty(self) -> bool:
        return not self._entries

    @classmethod
    def load_from_path(cls, path: str, threshold: float) -> "DrugCatalogService":
        """從靜態檔載入。

        檔案缺席或損毀時回傳空的服務而非拋錯：藥證庫不是啟動的必要條件，
        缺席的後果是所有藥名降為低信心（每份草稿都要人工核對），
        這個退化方向是安全的，讓應用起不來則不是。
        """
        try:
            with open(path, "r", encoding="utf-8") as catalog_file:
                raw_entries = json.load(catalog_file)
            entries = [
                DrugCatalogEntry(
                    license_number=item.get("license_number", ""),
                    name_zh=item.get("name_zh", ""),
                    name_en=item.get("name_en", ""),
                )
                for item in raw_entries
            ]
            service = cls(entries, threshold=threshold)
            # 檔案存在、也成功解析成 JSON，不代表內容真的可用——欄位名稱對不上
            # FDA 資料集（例如改版換了欄位名）會讓每個 item.get(...) 都拿到
            # 空字串，最終得到一個「載入成功」但條目數是 0 或每筆都是空殼的
            # DrugCatalogService，之後所有藥名都悄悄降為低信心，卻沒有任何
            # 錯誤訊息能讓人發現問題出在資料，而不是模型辨識不準。
            if service.is_empty:
                logger.warning(
                    "藥證庫載入完成但條目數為 0，所有藥名將降為低信心並強制人工核對："
                    "%s；請確認 FDA 資料集欄位名稱與載入邏輯是否對得上",
                    path,
                )
            else:
                logger.info("藥證庫載入完成：%s，共 %d 筆條目", path, len(entries))
            return service
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            logger.error(
                "藥證庫載入失敗，所有藥名將降為低信心並強制人工核對：%s（%s）",
                path,
                exc,
            )
            return cls([], threshold=threshold)

    def match(self, name: str) -> Optional[DrugCatalogMatch]:
        key = normalize_drug_name(name)
        if not key or not self._by_key:
            return None

        exact = self._by_key.get(key)
        if exact is not None:
            return self._to_match(exact, 1.0)

        if len(key) >= _MIN_CONTAINMENT_LENGTH:
            containment = self._match_by_containment(key)
            if containment is not None:
                return containment

        return self._match_by_fuzzy(key)

    def _candidates(self, key: str) -> set[str]:
        """從 gram 索引取候選鍵集合，含容比對與模糊比對共用同一份。

        交集（要求查詢字串的每個 gram 都出現在候選鍵的 gram 集合裡）
        精準對應「查詢字串是候選鍵的子字串」這個方向——這正是藥袋掃描
        最常見的情形（藥袋印短的品牌名，藥證是含劑型劑量的全名），且
        交集天生就小，不需要額外設上限。查詢裡完全查不到任何候選的
        gram（等同於「這串字跟藥證庫裡任何字串都沒有兩字重疊」）直接
        當作沒有貢獻、忽略，不讓它把交集清空成空集合——這只是放寬候選
        產生的精準度，之後含容比對仍會用真正的子字串關係驗證一次。

        另外聯集「查詢裡 postings 最少的那個 gram」，補上反方向（候選鍵
        是查詢字串的子字串）與模糊比對的召回率；設 postings 上限是為了
        不讓一個菜市場 gram 把候選集合撐回全庫規模。
        """
        grams = _ngrams(key)
        if not grams:
            return set()

        postings = [self._gram_index.get(gram, set()) for gram in grams]
        non_empty = [posting for posting in postings if posting]
        if not non_empty:
            return set()

        candidates = set.intersection(*non_empty) if len(non_empty) > 1 else set(non_empty[0])

        rarest = min(non_empty, key=len)
        if len(rarest) <= _MAX_RARE_GRAM_POSTINGS:
            candidates |= rarest

        return candidates

    def _match_by_containment(self, key: str) -> Optional[DrugCatalogMatch]:
        hits = [
            candidate_key
            for candidate_key in self._candidates(key)
            if key in candidate_key or candidate_key in key
        ]
        if not hits:
            return None

        entries_by_license: dict[str, DrugCatalogEntry] = {}
        for candidate_key in hits:
            entry = self._by_key[candidate_key]
            entries_by_license.setdefault(entry.license_number, entry)

        if len(entries_by_license) > 1:
            # 藥名驗證為真，但無法判斷是哪一張藥證——理由見
            # DrugCatalogMatch 的說明。score 沒有另外定義的意義，
            # 這裡不是相似度比對，留 0.0 只是滿足型別。
            return DrugCatalogMatch(license_number=None, name_zh="", name_en="", score=0.0)

        (entry,) = entries_by_license.values()
        # 含容覆蓋率：兩字串長度比值，短字串完全落在長字串裡時最高為 1.0。
        # 純粹供除錯／記錄參考，比對邏輯本身不看這個分數。
        coverage = max(
            min(len(key), len(candidate_key)) / max(len(key), len(candidate_key))
            for candidate_key in hits
            if self._by_key[candidate_key].license_number == entry.license_number
        )
        return self._to_match(entry, coverage)

    def _match_by_fuzzy(self, key: str) -> Optional[DrugCatalogMatch]:
        candidates = self._candidates(key)
        if not candidates:
            return None

        best_entry: Optional[DrugCatalogEntry] = None
        best_score = 0.0
        for candidate_key in candidates:
            score = SequenceMatcher(None, key, candidate_key).ratio()
            if score > best_score:
                best_entry, best_score = self._by_key[candidate_key], score

        if best_entry is None or best_score < self._threshold:
            return None
        return self._to_match(best_entry, best_score)

    @staticmethod
    def _to_match(entry: DrugCatalogEntry, score: float) -> DrugCatalogMatch:
        return DrugCatalogMatch(
            license_number=entry.license_number,
            name_zh=entry.name_zh,
            name_en=entry.name_en,
            score=score,
        )
