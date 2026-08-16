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
   SequenceMatcher，取門檻以上的最高分對應的鍵。**模糊命中只用來驗證
   藥名，永遠不確定身分**：回傳的 `license_number` 一律為 None、
   `candidates` 一律為空，理由見 `_match_by_fuzzy`。

含容比對與模糊比對的候選集合都來自同一份字元 n-gram 反向索引（建構子
裡建一次），不再對全部鍵做線性掃描——這是修正「模糊比對未命中時卡住
事件迴圈 400~750ms」缺陷的根本作法，細節見各方法的說明。查詢長度低於
`_MIN_CONTAINMENT_LENGTH` 時跳過含容比對（不查 n-gram 索引），只看
完全比對是否命中，避免極短查詢把半個藥證庫都拉進候選。

含容比對的兩個方向完整度不對稱，用兩種不同機制：「查詢是候選鍵的
子字串」由 n-gram 交集完整覆蓋（候選鍵必然含有查詢的每一個 gram，
見 `_candidates` 的說明）。「候選鍵是查詢的子字串」原本想用同一套
gram 索引近似（聯集查詢裡罕見的 gram），但驗證後發現這條路徑無法
被「provably 完整」：候選鍵越短、或候選鍵自己的 gram 在全庫剛好都很
常見，就會被系統性地漏掉——真實案例：食藥署藥品許可證庫裡有藥證
單獨以劑型或成分名稱掛證，中文的如「注射液」「膜衣錠100毫克」，
英文的如單獨掛證的原料藥名「TESTOSTERONE」「TESTOSTERONEPROPIONATE」，
長度從 2 字到 20 幾字都有，沒有一個長度上限能同時「provably 涵蓋
全部案例」又「窮舉集合夠小」。這個方向因此改用前綴分桶的精確多模式
比對（`_reverse_containment_hits`），不靠任何長度上限或頻率近似，
數學上保證找到每一個真正的子字串候選，見該方法的說明。

兩個方向的完整度補齊之後，還有另一件事不對稱：**證據力**。「查詢是
候選鍵的子字串」代表查詢字串整個對應到一張真實登記品名的一部分，是
「這是真實藥名」的證據；「候選鍵是查詢的子字串」只代表查詢字串裡
剛好包含某個登記過的片段（常見的是「膠囊」「膜衣錠」這類劑型名稱），
任何字串——包含模型讀錯、甚至完全虛構的藥名——只要恰好含有這類短詞
就會湊出這種命中，不構成「查詢字串本身是真實藥名」的證據。這條規則
有兩半，兩半都要成立：

1. **不能單獨建立驗證結果**：沒有 exact、也沒有 forward 命中時，即使
   reverse 命中非空，`match()` 仍回傳 None，見該方法的判斷式。
2. **不能成為可挑選的候選**：reverse 命中只能用來**拆掉**已經成立的
   唯一性（讓 `license_number` 留空），不得出現在 `candidates` 裡。
   `candidates` 是拿給使用者挑、且挑中就會貼上藥丸照片的清單，它的
   安全性建立在「候選皆受藥名約束，錯的候選仍是同名藥品」這個前提上
   （design.md 的 Risks）；reverse 命中按這個模組自己的定義就是**別的
   藥**——只是碰巧有個登記過的片段落在查詢字串裡。實測全庫 56,886 個
   中文品名，若把 reverse 命中一起放進 `candidates`，有 429 個藥名
   （0.75%）畫面上每一張照片都屬於別的藥、使用者自己那顆一張都沒有
   （「欲胃能錠」只有「胃能錠」有照片；「康保酊（辣椒酊）」只有
   「"明通" 辣椒酊」有照片），排除 reverse 後降到 233 個，剩下的全是
   forward 命中的同名藥品家族（不同廠牌或劑量），那是 spec 明訂的預期
   行為。這跟模糊路徑一律不帶候選是同一條理由，見 `_match_by_fuzzy`。

因此「唯一性判定用的集合」與「可挑選的候選集合」是兩份不同的集合：
前者是 exact ∪ forward ∪ reverse，後者只有 exact ∪ forward，
由 `_resolve` 分別承接，見該方法的說明。實測這個切分不改變任何一個
藥名的釘證結果（全庫 56,886 個中文品名，`license_number` 變動數為 0），
拿掉的只有「別的藥可以被挑」。

同一把尺量下去，**模糊命中的證據力比 reverse 更弱**：查詢字串在全庫
連一個字面命中都沒有，只是「長得像」某個鍵。它足以支撐藥名驗證（這正
是模糊比對存在的理由：把模型讀錯一個字的藥名救回來），但完全不足以
指認身分——所以模糊路徑一律不回傳 `license_number`、也不回傳候選，
見 `_match_by_fuzzy`。
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
    # 外觀欄位（來自食藥署藥品外觀資料集，見 scripts/build_drug_catalog.py）：
    # 無外觀記錄的藥證全部留空字串，不是 None——呼叫端不必先判斷型別
    # 才能安全串接顯示。原樣帶過原始資料，不在這裡正規化（例如 score_line
    # 常是字面上的「無」，color 可能是「黃;;;白」），正規化是呈現面的事。
    image_url: str = ""
    shape: str = ""
    color: str = ""
    score_line: str = ""
    mark_one: str = ""
    mark_two: str = ""
    size: str = ""


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
    字號。

    `candidates` **不是**上面那個聯集，是它的一個子集：只含完全比對與
    正向含容命中的藥證（依 license_number 去重），供呼叫端在唯一時直接
    用、在多筆時交給使用者挑一個。反向含容命中（品名是查詢字串子字串的
    那些藥證）算進唯一性判定、卻不列入 `candidates`——它們按定義是別的
    藥，不能拿給使用者挑，理由與量測見模組文件「證據力」那一段。
    因此 `len(candidates) == 1` **不蘊含** `license_number` 有值：這代表
    「只有一張藥證有資格被挑，但庫裡還有別的可能」，呈現面必須把它當成
    「請使用者確認」而不是「已確定」（見 LIFF 的 `DrugCandidateSection`）。
    反向的蘊含仍然成立：`license_number` 有值時 `candidates` 必然剛好
    一筆，見 `_resolve`。

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


def _has_identifying_content(key: str) -> bool:
    """鍵是否至少含有一個英數字或中日韓文字。

    原始資料裡混著純標點符號的資料瑕疵——整個品名就是「.」或「*」，
    這種鍵不具任何辨識力，卻會在反方向含容比對裡跟任何帶有這個符號的
    字串（例如英文縮寫的句點 "F.C. TABLETS"）湊出虛假的子字串命中：
    實測全庫正規化後的 112,230 個鍵裡只有這兩個純標點鍵（排除後
    `_by_key` 剩 112,228 個），但其中「.」單獨掛的
    那張證號（一款乾洗手液）讓抽樣的英文藥名裡有 88% 因為名稱帶句點
    而被拖進候選、平白失去原本能確定的證號。這種鍵在建索引時就該
    整個排除，而不是等到查詢時才特別處理。
    """
    return any(ch.isalnum() for ch in key)


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
        # 各佔一個鍵。實測全庫 66,478 筆藥證產生 112,228 個鍵（已扣掉下面
        # 排除的純標點鍵），其中 10,765 個鍵對應到不只一張藥證，涉及 21,250
        # 張不重複藥證（**全庫 32.0%**，不是先前寫的 47%——那個數字把碰撞
        # 鍵上的「鍵×藥證」出現次數當成藥證張數重複計了），
        # 用 setdefault 只留第一筆會讓其餘藥證永遠比對不到——這裡改成
        # 集合，讓碰撞的每一筆都保留、都可觸達；「保留多筆」跟「回傳哪一筆」
        # 是兩件事，後者留給 `match()` 依候選數量決定（見 `_resolve`）。
        # 純標點符號的鍵（見 `_has_identifying_content` 的說明）在這裡就
        # 排除，不進 `_by_key`——它不具辨識力，留著只會讓下游的反方向
        # 含容比對把它當成任何帶有該符號之字串的「候選」。
        self._by_key: dict[str, dict[str, DrugCatalogEntry]] = {}
        for entry in self._entries:
            for raw_name in (entry.name_zh, entry.name_en):
                key = normalize_drug_name(raw_name)
                if key and _has_identifying_content(key):
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

        # 前綴分桶：鍵的前兩個字元 → 有這個前綴的全部鍵（長度不足 2 字的
        # 鍵另外存進 `_single_char_keys`）。供 `_reverse_containment_hits`
        # 做「候選鍵是查詢字串子字串」這個方向的精確多模式比對——任何
        # 鍵若真的從查詢字串某個位置開始逐字相同，它的前兩個字元必然
        # 等於查詢字串同一個位置的前兩個字元，所以只要對查詢的「每一個
        # 起始位置」查這份分桶，就保證不漏掉任何真正的子字串候選，不必
        # 對全庫線性掃描。實測 `_by_key` 的 112,228 個鍵（扣掉 3 個單字元
        # 鍵，有 112,225 個進得了分桶）只有 21,088 種不同的兩字前綴，
        # 桶的中位數只有 1 筆、最大 1,692 筆，遠比逐鍵掃描全庫便宜。
        self._by_prefix: dict[str, list[str]] = {}
        self._single_char_keys: set[str] = set()
        for key in self._by_key:
            if len(key) == 1:
                self._single_char_keys.add(key)
            else:
                self._by_prefix.setdefault(key[:2], []).append(key)

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
                    # `.get(..., "")`：drug_catalog.json 是提交進 repo 的產出物，
                    # 舊 commit 產出的檔案沒有這些鍵，缺鍵時視為空字串而不是
                    # 讓載入失敗——外觀欄位是既有藥證資料的擴充，不是前提。
                    image_url=item.get("image_url", ""),
                    shape=item.get("shape", ""),
                    color=item.get("color", ""),
                    score_line=item.get("score_line", ""),
                    mark_one=item.get("mark_one", ""),
                    mark_two=item.get("mark_two", ""),
                    size=item.get("size", ""),
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
        forward_hits: set[str] = set()
        reverse_hits: set[str] = set()
        if len(key) >= _MIN_CONTAINMENT_LENGTH:
            # 含容比對兩個方向的證據力不對稱，不能等量齊觀：
            # - forward（查詢是候選鍵的子字串）代表查詢字串整個對應到
            #   一張真實登記品名的一部分——這是「藥名真實存在」的證據
            #   （藥袋印短的品牌名，藥證是含劑型劑量的全名，見模組文件）。
            # - reverse（候選鍵是查詢字串的子字串）只代表某個登記過的
            #   片段剛好出現在查詢字串裡——任何字串只要恰好包含一個
            #   登記過的短詞（例如劑型名稱「膜衣錠」），不論是模型讀錯
            #   還是完全虛構的藥名，都會湊出這種命中。這不構成「查詢
            #   字串本身是真實藥名」的證據，見 `_reverse_containment_hits`
            #   與下方判斷式的說明。
            forward_hits = self._forward_containment_hits(key)
            reverse_hits = self._reverse_containment_hits(key)

        if exact is not None or forward_hits:
            # 已有完全比對或 forward 命中，藥名驗證為真：這時候把
            # reverse 命中一起聯集進來，只會「拆掉」原本看似唯一的
            # 答案（「潔毒注射液」命中「注射液」單獨掛證、「冠脂妥
            # 膜衣錠10毫克」命中「膜衣錠」單獨掛證都是這種情形），不會
            # 讓一個原本沒證據的查詢無中生有變成有證據——聯集只能讓
            # 候選變多，不能讓候選從 0 變成非 0。
            # 兩個方向要分開傳，不能在這裡先聯集成一份：reverse 只參與
            # 唯一性判定，不得進入可挑選的候選清單（模組文件「證據力」
            # 那條規則的第 2 半），兩份集合由 `_resolve` 分別承接。
            return self._resolve_exact_and_containment(key, exact, forward_hits, reverse_hits)

        if reverse_hits:
            # 唯一的命中訊號只有 reverse：查詢字串不對應任何真實登記
            # 的品名、也不是其中一部分，純粹是剛好包含了某個登記片段
            # （常見的是劑型名稱，或資料瑕疵留下的雜訊短鍵）。這不構成
            # 「藥名已驗證」的證據，必須維持未驗證、回傳 None，不得
            # 落入模糊比對——原本的判斷式本來就是「exact 或
            # 任一方向的含容命中非空」就不進模糊比對，這裡延續同一個
            # 控制流程，只是不再讓 reverse-only 的命中被誤判成已驗證。
            return None

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

    def _reverse_containment_hits(self, key: str) -> set[str]:
        """精確找出全部「是查詢字串子字串」的鍵，不透過 gram 索引近似。

        這個方向沒有 gram 交集可用：候選鍵比查詢短，gram 數量本來就比
        查詢少，不可能涵蓋查詢的「每一個」gram。先前試過「聯集查詢裡
        （未超過頻率上限的）gram 的 postings」這個近似捷徑，仍然證明
        無法 provably 完整——候選鍵若自己的 gram 在全庫剛好都很常見
        （例如中文的「膜衣」「毫克」，或英文藥名裡到處都是的雙字母
        組合），就會被系統性地漏掉，且沒有一個長度上限能同時「涵蓋
        全部案例」又「窮舉集合夠小」（實測真實藥證庫裡單獨掛證的案例
        從 2 字的「膠囊」到 22 字的「TESTOSTERONEPROPIONATE」都有）。

        改用前綴分桶做精確多模式比對：候選鍵若真的從查詢字串某個位置
        開始逐字相同，它的前兩個字元必然等於查詢字串同一個位置的前
        兩個字元（`self._by_prefix` 正是用鍵的前兩個字元分桶）。所以
        對查詢字串的每一個起始位置，查一次分桶、對桶內候選逐一做切片
        比對確認是不是真的整段相同，就保證找到每一個真正的子字串候選，
        不必對全庫線性掃描——這是數學上的保證，不是機率上的近似。
        長度不足 2 字的鍵（`self._single_char_keys`）沒有完整的兩字
        前綴可分桶，另外在每個位置直接比對那一個字元。
        """
        hits: set[str] = set()
        length = len(key)
        for start in range(length):
            if key[start] in self._single_char_keys:
                hits.add(key[start])
            prefix = key[start : start + 2]
            if len(prefix) < 2:
                continue
            for candidate_key in self._by_prefix.get(prefix, ()):
                if key[start : start + len(candidate_key)] == candidate_key:
                    hits.add(candidate_key)
        return hits

    def _forward_containment_hits(self, key: str) -> set[str]:
        """找出「查詢是候選鍵的子字串」這個方向的候選鍵。

        由 `_candidates(key)` 的 gram 交集完整覆蓋——候選鍵必然含有
        查詢的每一個 gram，見該方法的說明。這個方向代表查詢字串整個
        對應到某個真實登記品名的一部分，是「藥名真實存在」的證據，
        跟 `_reverse_containment_hits` 只能用來拆掉唯一性、不能單獨
        建立驗證結果的地位不同，見 `match()` 的判斷式。
        """
        return {candidate_key for candidate_key in self._candidates(key) if key in candidate_key}

    def _resolve_exact_and_containment(
        self,
        key: str,
        exact: Optional[dict[str, DrugCatalogEntry]],
        forward_hits: Iterable[str],
        reverse_hits: Iterable[str],
    ) -> DrugCatalogMatch:
        """把三種命中攤平成「唯一性集合」與「可挑選集合」兩份。

        見 spec「證號唯一才可信」與 DrugCatalogMatch 的說明：完全比對
        精準命中一個鍵不代表消除了歧義，三種訊號要聯集之後才能交給
        `_resolve` 判定唯一性。命中鍵可能不只一個（「普拿疼」同時是
        三個品名的子字串），每個命中鍵本身也可能已經是碰撞鍵——都要
        攤平進同一份集合，唯一性判定才不會漏看任何一張藥證。

        但唯一性判定的集合不等於拿給使用者挑的集合：reverse 命中按
        定義是**別的藥**（只是碰巧有個登記過的片段落在查詢字串裡），
        它足以拆掉唯一性，卻不能當成「你吃的可能是這一顆」讓使用者
        挑——挑中就會貼上那顆藥的照片。因此這裡建兩份字典，
        `entries_by_license` 決定 `license_number`，`pickable_by_license`
        決定 `candidates`，兩者的差集正好是 reverse-only 的命中。
        理由與量測見模組文件「證據力」那一段。
        """
        forward_keys = set(forward_hits)
        reverse_keys = set(reverse_hits)

        entries_by_license: dict[str, DrugCatalogEntry] = {}
        for candidate_key in forward_keys | reverse_keys:
            entries_by_license.update(self._by_key[candidate_key])
        pickable_by_license: dict[str, DrugCatalogEntry] = {}
        for candidate_key in forward_keys:
            pickable_by_license.update(self._by_key[candidate_key])

        if exact is not None:
            entries_by_license.update(exact)
            # 完全比對是「查詢字串本身就是這張藥證的品名」，是三種命中裡
            # 證據力最強的一種，當然可挑。
            pickable_by_license.update(exact)
            # 完全比對存在時 score 固定 1.0（字串確實逐字相同），不論
            # 聯集後候選是不是唯一——這正是 score 1.0 不再等於證號確定
            # 的地方，由 `_resolve` 依候選數量決定 license_number。
            return self._resolve(entries_by_license.values(), 1.0, pickable_by_license.values())

        if len(entries_by_license) > 1:
            # 沒有完全比對、純靠含容比對湊出多張藥證：藥名驗證為真，
            # 但無法判斷是哪一張——理由見 DrugCatalogMatch 的說明。
            # score 沒有另外定義的意義，這裡不是相似度比對，留 0.0
            # 只是滿足型別。
            return self._resolve(entries_by_license.values(), 0.0, pickable_by_license.values())

        (entry,) = entries_by_license.values()
        # 含容覆蓋率：兩字串長度比值，短字串完全落在長字串裡時最高為 1.0。
        # 純粹供除錯／記錄參考，比對邏輯本身不看這個分數。兩個方向的命中
        # 都算進來（跟切分候選之前一樣），這個分數不參與任何判定。
        coverage = max(
            min(len(key), len(candidate_key)) / max(len(key), len(candidate_key))
            for candidate_key in forward_keys | reverse_keys
            if entry.license_number in self._by_key[candidate_key]
        )
        return self._resolve(entries_by_license.values(), coverage, pickable_by_license.values())

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
        # 模糊命中只證明「這個字串很像某個真實藥名」，不證明「這個字串**是**
        # 一個真實藥名」——它在藥證庫裡一個字面命中都沒有。依模組文件開頭
        # 那條同樣的規則（沒有證明查詢字串是真實品名的命中，不得用來確定
        # 身分），這裡一律不釘證號、也不帶候選：
        # - 不釘證號：量測方式是拿全庫 56,886 個中文品名當母體，固定亂數
        #   種子抽 6,000 筆，把正規化鍵裡的一個字換掉，再走完整條 match()
        #   控制流程，算「不設此限時會釘上的證號屬於別顆藥」的比率（spec
        #   「模糊比對只驗證藥名，不確定身分」記的是同一次量測）：
        #     替換一個**中文字**：0.18%（11/6,000）不論同分的哪一個鍵勝出
        #     都會釘到別顆藥，另有 0.15% 要看勝出的是哪一個，合計 0.33%。
        #     例：'安融寧錠2毫克'（真實品名 '安保寧錠２毫克' 換掉「保」）
        #     → '"約克"安寧錠2毫克'（衛署藥製字第038370號），score 0.923。
        #     替換一個**劑量數字**：0.48%（29/6,000）必然釘錯；連同視勝出
        #     者而定的同分案例，合計**約 2%**——這個合計刻意不寫到小數點
        #     第二位：它計入的正是同分案例，而同分誰勝出取決於下面那句話
        #     講的 set 迭代順序，同一份資料在不同行程量到的就是不同的數字
        #     （實測 PYTHONHASHSEED 0／1／42 分別落在 2.02%／2.00%／2.02%），
        #     寫成一個定值會讓讀者以為它可重現。比中文字高，因為同一個
        #     品名家族的不同劑量彼此只差一個數字。
        #     例：'思樂康持續性藥效錠30毫克'（真實品名 '思樂康持續性藥效錠
        #     50 毫克' 換掉劑量數字）→ '思樂康持續性藥效錠 300 毫克'
        #     （衛署藥輸字第024886號），score 0.963——同廠牌，劑量差 10 倍。
        #   兩個例子都確認過真的走得到這裡（查詢字串不是任何鍵，forward 與
        #   reverse 命中皆為空），且最高分只有一個鍵拿到，不受上面挑鍵時
        #   set 迭代順序的影響。「合計」與「必然」的差額就是同分的案例：
        #   最高分由多個鍵並列時，上面的迴圈只留「第一個嚴格更高分」的那個，
        #   誰勝出取決於 set 的迭代順序——同一個查詢在不同行程可能釘到不同
        #   藥證，連錯得穩不穩定都談不上。證號一釘就解析得出縮圖，推播與
        #   清單會貼上一張錯的藥丸照片，而畫面上沒有任何東西能讓長輩發現
        #   不對——貼錯照片比不貼照片危險。
        # - 不帶候選：候選機制的安全性建立在「候選皆受藥名約束，錯的候選
        #   仍是同名藥品」（design.md 的 Risks），使用者是在同名藥品之間
        #   挑。模糊路徑上的候選按定義就是**別的藥名**，這個前提不成立，
        #   不能拿來給使用者挑。
        # 名稱驗證完全不受影響：`_verify_against_catalog` 只看 `match()`
        # 是否為 None（prescription_scan_service.py），模糊比對回收錯讀
        # 藥名的能力原封不動保留，這裡放掉的只有「是哪一張藥證」。
        return DrugCatalogMatch(
            license_number=None, name_zh="", name_en="", score=best_score, candidates=[]
        )

    @staticmethod
    def _resolve(
        entries: Iterable[DrugCatalogEntry],
        score: float,
        pickable: Iterable[DrugCatalogEntry],
    ) -> DrugCatalogMatch:
        """把「命中鍵對應到的藥證集合」收斂成一筆比對結果。

        這是唯一決定 `license_number` 有沒有值的地方——證號是否確定只看
        `entries` 的數量是不是剛好 1，跟走的是哪個階段、score 是多少無關。

        `entries` 與 `pickable` 是**兩個不同的角色**，刻意分成兩個參數而
        不是一份集合兼任：

        - `entries`：唯一性判定用。包含 reverse 命中——它證據力不足以
          指認身分，卻足以證明「還有別的可能」，漏掉它會回傳一個錯的
          確定證號。
        - `pickable`：`candidates` 的來源，也就是畫面上會出現、使用者
          挑中就會貼上藥丸照片的清單。不含 reverse-only 的命中。

        `pickable` 沒有預設值是刻意的：預設成 `entries` 會讓任何新的
        呼叫端在忘記切分時，悄悄地把「別的藥」放回可挑清單，而這個
        退化沒有任何測試以外的地方看得出來（照片一貼上去，畫面上不會
        有東西反駁它）。要兩者相同就明講兩次。

        `entries` 唯一時 `pickable` 必然等於 `entries`：走到這裡的前提是
        exact 或 forward 命中非空（見 `match()` 的判斷式），所以 `pickable`
        非空，而 `pickable ⊆ entries`——「已釘證號卻沒有候選」這個狀態
        不可能出現。反過來不成立：`pickable` 剩一筆時 `entries` 可能仍有
        多筆，此時證號留空、候選只有一筆，由使用者確認才釘定
        （見 LIFF 的 `DrugCandidateSection`）。
        """
        entries = list(entries)
        candidates = list(pickable)
        if len(entries) == 1:
            (entry,) = entries
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
