"""藥證庫比對。

存在的理由只有一個：偵測視覺模型的錯讀。模型把「脈優錠」讀成形近的其他藥名時，
它自述的信心度仍然很高，唯一能發現該字串不對應任何一張核准藥證的方法，
就是拿去跟外部字典比對。比對不到即降為低信心，強制人工核對。

建構子接受已載入的條目而非路徑，測試才能直接餵小型固定資料集，
不必碰檔案系統，也不必 monkey patch。

比對分兩個階段（依序，第一階段有命中就不再往下）：

1. 完全比對 ∪ 含容比對：查詢字串等於某個鍵（完全比對），聯集上品名
   包含這個查詢字串的所有鍵（含容比對，方向不拘：查詢是候選的子字串，
   或候選是查詢的子字串）。這兩者要聯集成同一份候選集合、一起交給
   `_resolve` 判定唯一性，不能讓完全比對單獨決定答案——藥袋通常只印
   短的品牌名（「普拿疼」），藥證上卻是連劑型、劑量都在內的全名
   （「普拿疼錠500毫克」），但這個短名本身也可能剛好等於**另一張**
   藥證的完整品名（「得胎隆」完全比對只中一張，另有 3 張品名包含它，
   例如「亞培」得胎隆膜衣錠10毫克）。只看完全比對會把後者的存在視而
   不見，讓一次精準但仍有多個可能的命中，看起來像已經確定——這正是
   score 1.0 卻比錯藥證的主因，見 DrugCatalogMatch 的說明。
   （單純用 SequenceMatcher 比長度差懸殊的兩個字串，比值會被長度差
   拉低到任何門檻都分不開「這是同一顆藥的縮寫」跟「這是兩個不相干的
   字串」——實測 '普拿疼' 對 '普拿疼錠500毫克' 只有 0.500——含容比對
   繞過這個問題：直接檢查子字串關係。）
2. 模糊比對：完全比對與含容比對都沒有任何命中時，對候選做
   SequenceMatcher，取門檻以上的最高分對應的鍵。

含容比對與模糊比對的候選集合都來自同一份字元 n-gram 反向索引（建構子
裡建一次），不再對全部鍵做線性掃描——這是修正「模糊比對未命中時卡住
事件迴圈 400~750ms」缺陷的根本作法，細節見各方法的說明。查詢長度低於
`_MIN_CONTAINMENT_LENGTH` 時跳過含容比對（不查 n-gram 索引），只看
完全比對是否命中，避免極短查詢把半個藥證庫都拉進候選。
"""

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
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

    `license_number` 是 Optional，這個 None 代表候選集合裡有不只一張
    藥證字號。候選集合是「完全比對命中的鍵」跟「品名包含查詢字串的鍵」
    的**聯集**——兩者缺一都會漏掉真正的歧義：只看完全比對，會漏掉查詢
    字串剛好也是另一張藥證全名一部分的情形（「得胎隆」完全比對只中
    一張，但另有 3 張品名包含它，例如「亞培」得胎隆膜衣錠10毫克，
    真正在藥袋上的可能是後者）；只看含容比對，則會漏掉查詢字串自己
    就精準等於另一張藥證品名的情形（「普拿疼」同時是好幾個普拿疼系列
    產品品名的子字串）。**完全比對命中（`score == 1.0`）不是例外**：
    查詢字串跟某個鍵逐字相同，只代表這個字串本身是一個真實藥名，不
    代表消除了歧義——「感冒液」完全比對命中時就對應 41 張藥證。這種
    情況下藥名本身已經被驗證為真實存在、核准過的藥品——這正是本比對
    唯一的存在理由——只是不知道對應哪一個品項，因此不得任意選一個
    冒充答案（選第一個、選最短的、甚至選 score 最高的都是編造），
    寧可留空也不要讓使用者的用藥提醒掛上一個他根本沒有被開立的藥證
    字號。`candidates` 帶出聯集後的全部藥證（依 license_number 去重），
    供呼叫端在唯一時直接用、在多筆時交給使用者挑一個。

    呼叫端判斷「這個藥名有沒有通過藥證庫校驗」必須看 `match()` 的回傳
    值是不是 None，而不是看 `license_number` 是不是 None——後者只表示
    「知不知道是哪一張藥證」，是校驗結果之外的另一個維度，把兩者混在
    一起會讓含容或完全比對命中但無法定位品項的藥名被誤判成未驗證，
    重新強制走人工核對，抵銷了候選機制原本要解決的問題。
    """

    license_number: Optional[str]
    name_zh: str
    name_en: str
    score: float
    candidates: list[DrugCatalogEntry] = field(default_factory=list)


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
        # 正規化後的鍵 → { license_number → 條目 }。同一條目的中英文品名
        # 各佔一個鍵。實測全庫 66,478 筆藥證產生 112,230 個鍵，其中 10,766
        # 個鍵對應到不只一張藥證（涉及全庫 47% 的藥證，見模組文件），
        # 用 setdefault 只留第一筆會讓其餘藥證永遠比對不到——這裡改成
        # 集合，讓碰撞的每一筆都保留、都可觸達；「保留多筆」跟「回傳哪一筆」
        # 是兩件事，後者留給 `match()` 依候選數量決定（見 `_resolve`）。
        self._by_key: dict[str, dict[str, DrugCatalogEntry]] = {}
        for entry in self._entries:
            for raw_name in (entry.name_zh, entry.name_en):
                key = normalize_drug_name(raw_name)
                if key:
                    self._by_key.setdefault(key, {})[entry.license_number] = entry

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
        # 含容比對的候選鍵：品名跟查詢字串有子字串關係（不拘方向）。
        # 跟完全比對一起聯集，不能讓完全比對單獨決定答案——查詢字串
        # 精準命中一個鍵，不代表它不會同時是另一張藥證全名的一部分
        # （「得胎隆」完全比對只中一張，但另有 3 張品名包含它）。低於
        # 最小長度時跳過（不查 n-gram 索引），避免極短查詢拉進半個
        # 藥證庫，見 `_MIN_CONTAINMENT_LENGTH` 的說明。
        containment_hits = (
            self._containment_hit_keys(key) if len(key) >= _MIN_CONTAINMENT_LENGTH else []
        )

        if exact is not None or containment_hits:
            return self._resolve_exact_and_containment(key, exact, containment_hits)

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

    def _containment_hit_keys(self, key: str) -> list[str]:
        """從 gram 索引取出跟查詢字串有子字串關係（任一方向）的鍵。

        只負責找出「品名有子字串關係」的候選鍵，不做任何唯一性判定——
        判定交給 `_resolve_exact_and_containment`，這裡回傳的鍵清單會
        跟完全比對的結果聯集。
        """
        return [
            candidate_key
            for candidate_key in self._candidates(key)
            if key in candidate_key or candidate_key in key
        ]

    def _resolve_exact_and_containment(
        self,
        key: str,
        exact: Optional[dict[str, DrugCatalogEntry]],
        containment_hits: list[str],
    ) -> DrugCatalogMatch:
        """把「完全比對命中的鍵」跟「品名包含查詢字串的鍵」聯集成同一份候選。

        見 spec「證號唯一才可信」與 DrugCatalogMatch 的說明：完全比對
        精準命中一個鍵不代表消除了歧義，兩種訊號要聯集之後才能交給
        `_resolve` 判定唯一性。命中鍵可能不只一個（「普拿疼」同時是
        三個品名的子字串），每個命中鍵本身也可能已經是碰撞鍵——都要
        攤平進同一份候選集合，唯一性判定才不會漏看任何一張藥證。
        """
        entries_by_license: dict[str, DrugCatalogEntry] = {}
        for candidate_key in containment_hits:
            entries_by_license.update(self._by_key[candidate_key])

        if exact is not None:
            entries_by_license.update(exact)
            # 完全比對存在時 score 固定 1.0（字串確實逐字相同），不論
            # 聯集後候選是不是唯一——這正是 score 1.0 不再等於證號確定
            # 的地方，由 `_resolve` 依候選數量決定 license_number。
            return self._resolve(entries_by_license.values(), 1.0)

        if len(entries_by_license) > 1:
            # 沒有完全比對、純靠含容比對湊出多張藥證：藥名驗證為真，
            # 但無法判斷是哪一張——理由見 DrugCatalogMatch 的說明。
            # score 沒有另外定義的意義，這裡不是相似度比對，留 0.0
            # 只是滿足型別。
            return self._resolve(entries_by_license.values(), 0.0)

        (entry,) = entries_by_license.values()
        # 含容覆蓋率：兩字串長度比值，短字串完全落在長字串裡時最高為 1.0。
        # 純粹供除錯／記錄參考，比對邏輯本身不看這個分數。
        coverage = max(
            min(len(key), len(candidate_key)) / max(len(key), len(candidate_key))
            for candidate_key in containment_hits
            if entry.license_number in self._by_key[candidate_key]
        )
        return self._resolve(entries_by_license.values(), coverage)

    def _match_by_fuzzy(self, key: str) -> Optional[DrugCatalogMatch]:
        candidates = self._candidates(key)
        if not candidates:
            return None

        # 先選出相似度最高的「鍵」，再取該鍵對應的全部藥證當候選——
        # 挑鍵的邏輯跟原本一樣（第一個嚴格超過目前最高分的才換），只是
        # 命中的鍵本身可能對應不只一張藥證，唯一性判定交給 `_resolve`。
        best_key: Optional[str] = None
        best_score = 0.0
        for candidate_key in candidates:
            score = SequenceMatcher(None, key, candidate_key).ratio()
            if score > best_score:
                best_key, best_score = candidate_key, score

        if best_key is None or best_score < self._threshold:
            return None
        return self._resolve(self._by_key[best_key].values(), best_score)

    @staticmethod
    def _resolve(entries: Iterable[DrugCatalogEntry], score: float) -> DrugCatalogMatch:
        """把「命中鍵對應到的藥證集合」收斂成一筆比對結果。

        這是唯一決定 `license_number` 有沒有值的地方，兩個比對階段
        （完全∪含容／模糊）都先取到候選集合才呼叫這裡——證號是否確定
        只看候選數量是不是剛好 1，跟走的是哪個階段、score 是多少無關。
        `candidates` 一律帶出完整候選集合（唯一時也是只含一筆的清單），
        供呼叫端在多筆時交給使用者挑一個。
        """
        candidates = list(entries)
        if len(candidates) == 1:
            (entry,) = candidates
            return DrugCatalogMatch(
                license_number=entry.license_number,
                name_zh=entry.name_zh,
                name_en=entry.name_en,
                score=score,
                candidates=candidates,
            )
        return DrugCatalogMatch(
            license_number=None, name_zh="", name_en="", score=score, candidates=candidates
        )
