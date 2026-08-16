"""用藥風險判定的純函式。

本模組 SHALL NOT 有任何 I/O、類別或模組層級狀態：藥證庫一律以參數傳入。
理由是通報家人不可逆——訊息推出去收不回來，收件人是一整個家庭——這種決定
不能取決於單次模型呼叫的輸出穩定性。判定留在這裡，就能用 table-driven test
把每一格門檻永久釘住，也能在不呼叫任何外部服務的情況下重現。
"""

import re

from app.models.safety import DrugMention, RiskLevel
from app.services.medication.drug_catalog_service import (
    DrugCatalogService,
    normalize_drug_name,
)

# 非中文字符集的偵測區間。以字元判斷而非模型自述語言：模型可能判錯，字元不會。
# 中文藥證品名不可能含假名，日文藥品包裝幾乎必然含假名，所以這是事實判斷，
# 不是追不完的關鍵字黑名單。
#
# 拉丁字母刻意不在此列：台灣核准藥證的英文品名本來就是拉丁字母（LIPITOR、
# PANADOL），列入會全面誤報。代價是歐美代購偵測不到，屬已知覆蓋缺口。
_FOREIGN_SCRIPTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ja", re.compile(r"[぀-ゟ゠-ヿ]")),  # 平假名、片假名
    ("ko", re.compile(r"[가-힯]")),
    ("th", re.compile(r"[฀-๿]")),
)

# 取得通路本身即構成風險的三種，與藥名核准與否無關。
_UNTRUSTED_CHANNELS = frozenset({"tv_shopping", "acquaintance", "online_marketplace"})

# 藥品調劑包裝的法定必載欄位（衛署藥字第0910033863號）。四項同時出現才視為
# 合法醫療機構調劑；只出現其中一兩項（OCR 常見）不足以證明，仍照常判定。
DISPENSED_PACKAGE_MARKERS: tuple[str, ...] = (
    "patient_name",
    "institution",
    "dispenser",
    "dispensed_date",
)

# 前置篩選的關鍵詞。黑名單性質、永遠追不完，但漏接的方向是「該偵測的沒偵測」，
# 不會產生誤報，因此刻意維持小而明確，不塞進「吃」「喝」這類日常字。
_DRUG_KEYWORDS: tuple[str, ...] = (
    "藥",
    "錠",
    "膠囊",
    "藥丸",
    "藥水",
    "藥膏",
    "藥粉",
    "保健食品",
    "營養品",
    "維他命",
    "代購",
    "成藥",
    "偏方",
    "處方",
    "副作用",
    "劑量",
    "服用",
    "口服",
)

# 藥名候選的切分：把非字母、數字、漢字與假名的字元一律當成分隔符。
_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z一-鿿぀-ヿ]+")

# 候選探測的視窗大小與次數上限。藥袋 OCR 全文可能上千字，不設上限會讓前置
# 篩選本身變成延遲來源——而它存在的理由正是要省成本。
_PROBE_WINDOW_SIZES: tuple[int, ...] = (3, 4, 5)
_MIN_PROBE_LENGTH = 3
_MAX_CATALOG_PROBES = 60


def detect_foreign_scripts(text: str) -> list[str]:
    """回傳文字中出現的非中文字符集代碼，順序固定、每個代碼最多一次。"""
    if not text:
        return []
    return [code for code, pattern in _FOREIGN_SCRIPTS if pattern.search(text)]


def assess(mention: DrugMention, foreign_scripts: list[str]) -> RiskLevel:
    """依藥證庫比對結果與取得訊號決定風險等級。

    「藥證庫查無即未核准」是錯的：查無最常見的原因是俗稱、簡稱或錯字。反過來
    「藥證庫查得到就安全」也是錯的：合利他命強効錠 EX PLUS 會含容命中我國核准
    的同名藥證，而它正是最典型的境外代購案例。因此兩個維度必須合看。
    """
    if mention.channel in _UNTRUSTED_CHANNELS:
        return "high"

    if foreign_scripts or mention.channel == "overseas_personal":
        return "high"

    if mention.catalog_hit:
        return "none"

    # 這裡是刻意保守的一格：證據只有「藥證庫查無」時不足以驚動全家。
    # 帶有完整調劑包裝訊號時連當事人都不打擾——他剛拍的就是包裝，再請他
    # 「拍一下包裝給我看」是這個功能最常見、也最沒有意義的誤報。
    if _has_dispensed_package_markers(mention):
        return "none"

    return "low"


def looks_drug_related(text: str, catalog: DrugCatalogService) -> bool:
    """前置篩選：不呼叫模型，擋掉與藥品無關的訊息。

    藥證庫以參數傳入，模組載入時不讀任何檔案。缺席（載入失敗）時仍以關鍵詞
    運作，不拋例外——退化方向是少偵測，不是誤報。
    """
    if not text:
        return False

    if any(keyword in text for keyword in _DRUG_KEYWORDS):
        return True

    if catalog is None or catalog.is_empty:
        return False

    return any(catalog.match(probe) is not None for probe in _catalog_probes(text))


def normalize_drug_key(name: str) -> str:
    """節流用的藥名鍵。

    直接沿用藥證庫的正規化，讓「合利他命EX PLUS」與「合利他命 EX PLUS」落在
    同一筆。另寫一套會讓節流的鍵與比對的鍵悄悄分歧。
    """
    return normalize_drug_name(name)


def _has_dispensed_package_markers(mention: DrugMention) -> bool:
    return set(DISPENSED_PACKAGE_MARKERS).issubset(set(mention.dispensed_package_markers))


def _catalog_probes(text: str):
    """產生要拿去比對藥證庫的候選字串，總數受 `_MAX_CATALOG_PROBES` 限制。

    先整個 token（英文藥名多半自成一個 token），再對較長的 token 滑動視窗
    （中文句子裡的藥名不會被空白切出來）。
    """
    probes = 0
    for token in _TOKEN_SPLIT.split(text):
        if len(token) < _MIN_PROBE_LENGTH:
            continue

        if probes >= _MAX_CATALOG_PROBES:
            return
        probes += 1
        yield token

        for size in _PROBE_WINDOW_SIZES:
            for start in range(len(token) - size + 1):
                if probes >= _MAX_CATALOG_PROBES:
                    return
                probes += 1
                yield token[start : start + size]
