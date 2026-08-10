"""RAG 入庫／核准的唯一信任邊界：判斷一個 URL 能不能被當成參考來源。

設計依據：openspec/changes/harden-url-whitelist/design.md 的 Decision 1／2／3／4／6／7。

核心策略是「canonicalize 後比對」而非黑名單過濾：把輸入轉成一個受限文法內的
唯一字串（normalize），轉不出來就回 None；is_allowed 只認 normalize 過的結果。
理由見 Decision 1——黑名單要列舉的是「已知會造成解析歧異的字元」，那份清單由
我們不控制的下游解析器（Firecrawl 用的 Node、admin 的瀏覽器）定義，永遠補不完，
漏掉的預設是放行；canonicalize + 比對把預設翻過來：沒想到的攻擊手法預設拒絕。

此模組 SHALL NOT import fastapi、SHALL NOT import i18n（Decision 7）——它是純函式
模組，讓它知道 HTTP 狀態碼會把信任邊界綁死在一個 transport 上。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from app.core.config import settings

logger = logging.getLogger(__name__)

# 判準先於清單，見 design.md Decision 4：機構層級的權威性、內容穩定可長期存取、
# 無商業銷售動機、註冊門檻構成實質限制、對台灣使用者的可用性，五條全中才收。
DEFAULT_ALLOWED_DOMAIN_SUFFIXES: tuple[str, ...] = (
    "gov.tw",
    "nhri.edu.tw",
    "who.int",
    "cdc.gov",
    "nih.gov",
    "medlineplus.gov",
)

# 追蹤參數清單逐字取自 design.md Decision 2：同一頁從 LINE 分享出來會帶不同的
# utm，不剝掉就是同一頁重複入庫。
_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gclid",
        "fbclid",
        "msclkid",
        "yclid",
        "igshid",
        "mc_cid",
        "mc_eid",
    }
)

_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}

# host 的正字集規則：只允許 RFC 1123 主機名字元（小寫字母、數字、連字號），
# 標籤不得為空、不得以連字號開頭或結尾。
#
# 這條規則是 code review 後補上的（見 task-1-report.md Important 4）：原本只
# 針對 authority 含 '%' 這一個症狀個別擋（"evil.com%5C.gov.tw" 字面上就是以
# ".gov.tw" 結尾，會騙過標籤邊界比對，而不動點檢查對它是穩定的、抓不到），
# 但那是在列舉症狀、不是在陳述規則——host 除了 '%' 之外，還可能出現
# '<'／'>'／'^'／'|' 這類 DNS 上解析不到、Node 與瀏覽器都會拒絕剖析的字元，
# 或是空標籤（"https://.gov.tw/"、"https://a..gov.tw/"）。這些字面上雖然
# 「不可利用」（沒有任何下游解析器會把它們解成別的 host），但直接違反
# design.md 的 Goal：「任何我們放行的字串，其 host 在 Python、Node、瀏覽器
# 三者的解讀必須一致」——不一致的前提是三者都要能剖析，這些字串 Node 根本
# 剖析不了。改成正向規則（只允許已知安全的字元集）一次涵蓋以上全部，
# 包含原本的 '%' 檢查，故該檢查已移除、理由併入這裡。
#
# 註：底線 '_' 刻意不在允許字元集內（RFC 1123 主機名不允許底線）。這是刻意
# 的收斂，實作前已確認 resources/medical_anti_fraud_seed_urls.txt 與既有測試
# 中沒有任何 host 含底線的真實網址，故不會誤擋現有資料。
#
# 註：結尾是 `+` 而非 `*`，也就是 host 至少要有一個 '.'。單標籤主機名
# （localhost、intranet 主機、單純打錯的 "gov"）不是公開網址，一律歸為
# malformed 而非 not_allowed。兩者在安全上等價（都拒絕），但原因碼會被
# 下游拿去對映成表單錯誤訊息：使用者打 localhost 該看到「這不是合法的
# 公開網址」，而不是「這個來源不在收錄清單」。
_HOST_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+"
)


@dataclass(frozen=True)
class InvalidUrl:
    url: str
    reason: Literal["malformed", "not_allowed"]


class UrlNotAllowedError(Exception):
    """assert_allowed 失敗時拋出；資料在這裡，文案交給呈現層組（Decision 7）。"""

    def __init__(self, invalid: list[InvalidUrl]):
        self.invalid = invalid
        super().__init__(f"{len(invalid)} 個網址未通過來源白名單")


def _strip_tracking_params(query: str) -> str:
    """剝除追蹤參數，其餘 query 保持原字面順序與原始編碼。

    刻意不用 parse_qsl + urlencode：那會把每個 value 解碼再依 urlencode 的
    規則重新編碼，一來會把中文網址變成人眼不可讀的 %E9%AB%98...（design.md
    Decision 2 明文禁止對 query 做百分比編解碼），二來會改變沒被剝除的參數
    的字面形式，破壞「不做的事」清單。這裡只在 '&' 邊界切字串、只看 key。
    """
    if not query:
        return ""
    kept = [pair for pair in query.split("&") if pair.split("=", 1)[0] not in _TRACKING_PARAMS]
    return "&".join(kept)


def _compute_once(raw: str) -> tuple[str, str] | None:
    """跑一次完整的剖析與序列化，回傳 (序列化後的 URL, 認定的 host)。

    不含不動點檢查（那是呼叫端 _normalize_url 的責任，因為需要呼叫這個函式
    兩次來比較）。轉不出來一律回 None，不拋例外。
    """
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None

    # --- 剖析前的前置檢查（design.md Decision 1／tasks.md 1.4）---
    # 為什麼一定要在「剖析之前」：Python 的 urlsplit 會靜默刪除 \t／\r／\n
    # （CPython 3.6.14 之後為對齊 WHATWG 而加的行為）。
    # "evil.com\t.gov.tw" 經過 urlsplit 後 host 是 "evil.com.gov.tw"——一個
    # 完全合法、且會通過不動點檢查的正規字串。正規化器自己會把攻擊字串洗
    # 乾淨，所以控制字元與空白必須在剖析前擋，正規化後就來不及了。
    if "\\" in s:
        # 反斜線：WHATWG 把它當路徑分隔符、Python 當一般 host 字元；
        # "evil.com\.gov.tw" 兩邊解析器給出不同的 host。
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in s):
        return None
    if any(ch.isspace() or ch == "\xa0" for ch in s):
        return None

    # --- 無 scheme 時補 https:// ---
    if "://" not in s and not s.lower().startswith(("http:", "https:")):
        s = "https://" + s

    try:
        parsed = urlsplit(s)
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return None

    netloc = parsed.netloc
    if "@" in netloc:
        # userinfo：兩邊解析器都會把 @ 前面丟掉當成帳密，但
        # "www.hpa.gov.tw@evil.com" 貼在審核頁上人眼會讀成 hpa.gov.tw。
        return None
    if not netloc.isascii():
        # 見 Decision 3：authority 一律要求 ASCII，先不支援 IDN。
        # 'evil.com。gov.tw'.encode('idna') 會把 U+3002 當標籤分隔符，
        # 與 Node 的 new URL().host 一致，放行等於引入同形異義字風險。
        return None

    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        # 例如 port 不是數字（"javascript:alert(1)" 補 scheme 後，
        # netloc 會被誤判成 "javascript:alert(1)"，port 轉 int 失敗）。
        return None

    if not host:
        return None
    host = host.lower().rstrip(".")
    if not host:
        return None
    if not _HOST_RE.fullmatch(host):
        # 正字集規則：只允許 RFC 1123 主機名字元。見上方 _HOST_RE 的註解，
        # 這一條同時涵蓋 '%'、'<'／'>'／'^'／'|'、空標籤等所有「Node／瀏覽器
        # 根本剖析不了的字元」，不需要為每一種症狀各寫一條檢查。
        return None

    default_port = _DEFAULT_PORTS.get(scheme)
    port_suffix = "" if port is None or port == default_port else f":{port}"

    # fragment 直接丟棄，不進序列化。
    kept_query = _strip_tracking_params(parsed.query)
    query_suffix = f"?{kept_query}" if kept_query else ""

    path = parsed.path
    if not path:
        path = "/"
    else:
        # 用 rstrip 一次收斂全部尾端斜線（而非只去一個），與下面 host 的
        # rstrip(".") 用同一種手法。code review 發現原本用 path[:-1] 只去
        # 一個尾斜線，導致 "/x//" 這種輸入不冪等（第一次只變成 "/x/"，還要
        # 再正規化一次才會變成 "/x"），被不動點檢查誤判成 malformed——使用者
        # 只是多打了一個斜線，不該被當成格式錯誤。
        path = path.rstrip("/") or "/"
    # 刻意不做：不解析 . / .. 路徑段、不對 path 做百分比編解碼——路徑不影響
    # host，對信任邊界零貢獻；改寫反而可能把好好的網址改成 404（Decision 2）。

    out = f"{scheme}://{host}{port_suffix}{path}{query_suffix}"
    return out, host


def _normalize_url(raw: str) -> str | None:
    """純函式：把輸入正規化成受限文法內的唯一字串，轉不出來回 None。"""
    first = _compute_once(raw)
    if first is None:
        return None
    out, host = first

    # --- 不動點檢查（design.md Decision 1／tasks.md 1.4 最後一步）---
    # normalize(out) == out 且 urlsplit(out).hostname 必須等於這次認定的
    # host，任一不成立就拒絕。這是前置檢查之外的第二道防線，抓的是「這次
    # 序列化出來的字串，重新剖析後會不會變成別的東西」。
    #
    # 註（code review Important 2）：修完 path 的多尾斜線收斂（見下方
    # rstrip("/") 的註解）之後，_compute_once 內的每一步轉換（strip、
    # scheme／host 小寫、host 去尾點、port 去預設值、query 剝追蹤參數、path
    # 去尾斜線）單獨看都是「套一次就到終態」，理論上組合起來也會是套一次就
    # 穩定。實測用結構化案例＋約 8 萬筆隨機模糊測試都沒找到任何一個輸入是
    # 「只靠這道檢查才會被擋」（見 task-1-report.md）。仍然保留這道檢查，
    # 是因為它的存在理由本來就不是「擋今天已知的案例」，而是 Decision 1
    # 講的「沒想到的攻擊手法預設拒絕」——它是防未來有人改動上面任一步驟、
    # 不小心引入新的不冪等轉換時的安全網，不是防今天已知的輸入。
    second = _compute_once(out)
    if second is None:
        return None
    out2, host2 = second
    if out2 != out or host2 != host:
        return None

    return out


@dataclass(frozen=True)
class UrlPolicy:
    """允許清單策略。用建構子注入 allowed_suffixes，測試不必碰 settings。"""

    allowed_suffixes: tuple[str, ...]

    def normalize(self, raw: str) -> str | None:
        return _normalize_url(raw)

    def is_allowed(self, raw: str) -> bool:
        normalized = self.normalize(raw)
        if normalized is None:
            return False
        host = urlsplit(normalized).hostname or ""
        for suffix in self.allowed_suffixes:
            # 標籤邊界比對：host == suffix 或 host.endswith("." + suffix)。
            # 不可用裸 endswith，否則 "evilgov.tw" 會被 "gov.tw" 誤判通過。
            if host == suffix or host.endswith("." + suffix):
                return True
        return False

    def assert_allowed(self, urls: list[str]) -> list[str]:
        """回傳正規化後清單；失敗時走完全部 URL 才一次拋出。"""
        normalized_urls: list[str] = []
        invalid: list[InvalidUrl] = []
        for url in urls:
            normalized = self.normalize(url)
            if normalized is None:
                invalid.append(InvalidUrl(url=url, reason="malformed"))
                continue
            if not self.is_allowed(url):
                invalid.append(InvalidUrl(url=url, reason="not_allowed"))
                continue
            normalized_urls.append(normalized)
        if invalid:
            raise UrlNotAllowedError(invalid)
        return normalized_urls


def _clean_suffix_list(raw: str) -> list[str]:
    """逗號分隔、trim、小寫、去前導 '.' 與 '*.'、去空項、去重，保持穩定順序。"""
    items: list[str] = []
    for part in raw.split(","):
        s = part.strip().lower()
        if s.startswith("*."):
            s = s[2:]
        s = s.lstrip(".")
        if not s:
            continue
        if s not in items:
            items.append(s)
    return items


def _collapse_redundant(items: list[str]) -> tuple[str, ...]:
    """丟掉被清單中其他後綴完全涵蓋的冗餘項（保持穩定順序）。"""
    result = [s for s in items if not any(s != other and s.endswith("." + other) for other in items)]
    return tuple(result)


def parse_allowed_suffixes(raw: str) -> tuple[str, ...]:
    """解析 RAG_ALLOWED_DOMAIN_SUFFIXES 的原始字串。空字串回內建預設。"""
    if not raw or not raw.strip():
        return DEFAULT_ALLOWED_DOMAIN_SUFFIXES
    cleaned = _clean_suffix_list(raw)
    return _collapse_redundant(cleaned)


@lru_cache(maxsize=1)
def default_url_policy() -> UrlPolicy:
    """production 用的單例：讀 settings.RAG_ALLOWED_DOMAIN_SUFFIXES，模組層快取。"""
    raw = settings.RAG_ALLOWED_DOMAIN_SUFFIXES
    suffixes = parse_allowed_suffixes(raw)
    if raw and raw.strip():
        cleaned = _clean_suffix_list(raw)
        dropped = [s for s in cleaned if s not in suffixes]
        if dropped:
            # 載入時若發生冗餘收斂就 log 一行，否則營運看設定會誤以為
            # 逐字生效（design.md Decision 4／tasks.md 2.3）。
            logger.info(
                "RAG_ALLOWED_DOMAIN_SUFFIXES 已收斂，以下後綴被其他後綴涵蓋而略過：%s",
                ", ".join(dropped),
            )
    return UrlPolicy(allowed_suffixes=suffixes)


def normalize_url(raw: str) -> str | None:
    """模組層薄包裝，委派給 default_url_policy()。"""
    return default_url_policy().normalize(raw)


def is_allowed_url(url: str) -> bool:
    """模組層薄包裝，委派給 default_url_policy()。"""
    return default_url_policy().is_allowed(url)


def assert_allowed_urls(urls: list[str]) -> list[str]:
    """模組層薄包裝，委派給 default_url_policy()。"""
    return default_url_policy().assert_allowed(urls)


# 網搜的 site: 篩選與入庫白名單解耦（design.md Decision 5）：清單擴充後
# 「所有後綴皆屬 *.gov.tw」的推導不再成立，兩者各自讀各自的設定。
# 保留這個模組層常數是為了不動既有測試 test_with_whitelist_site_filter_appends_gov_tw。
WHITELIST_SEARCH_SITE_FILTER: str = settings.RAG_WEB_SEARCH_SITE_FILTER


def with_whitelist_site_filter(query: str) -> str:
    """為網搜 query 附加白名單 site 限制（已含 site: 則不重複加）。"""
    q = (query or "").strip()
    if not q:
        return q
    if "site:" in q.lower():
        return q
    return f"{q} {settings.RAG_WEB_SEARCH_SITE_FILTER}"
