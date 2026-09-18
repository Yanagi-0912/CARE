"""從電視新聞畫面的標題，找回那則新聞的原始報導。

長輩拍下來的畫面只有字，沒有網址。查核卡上的來源是查核報告或知識庫文章，
回答不了「我看到的那則新聞本身在哪裡」——這支服務補的就是那個連結。

**寧可不附，也不附錯的。** 附上別則新聞比沒有連結更糟：長輩會以為那就是他
看到的報導，而我們沒有任何機制讓他發現接錯了。所以兩道條件都要過：

1. 出處必須是電視台自己的新聞網站（`CHANNEL_DOMAINS`），或電視台自己上傳的
   YouTube 影片（網域是 YouTube 且標題裡有台名）。
2. 螢幕上的標題要幾乎整句出現在文章標題裡（`MATCH_THRESHOLD`）。

**台別認不出來時仍然查**，只是門檻拉高到 `UNKNOWN_CHANNEL_THRESHOLD`，而且
接受任何一台自己的網站。畫面糊掉、台標被切掉、或那台不在 n8n 的清單裡都會
沒有台別——2026-09-18 線上兩次電視新聞查核就都是 `channel=-`，舊版因此連搜都
沒搜。標題夠獨特時，哪一台播的可以由搜尋結果自己回答。

**刻意不收轉載站**（Yahoo、LINE TODAY、Facebook）與別家媒體：2026-09-18 的
實測裡，TVBS 那則「38歲洗腎男路倒不治」在中時有一篇 0.83 分的報導，那是別家
記者寫的同一件事，不是長輩看到的那一則；Yahoo／LINE TODAY 雖然多半是原文
轉載，但從搜尋結果分不出「授權轉載」與「別家報導同一事件」，一律不收。

標題比對只看標題、不看搜尋摘要：摘要裡常常整句引用**別則**新聞的標題
（實測中 TVBS 的「晨間快訊」摘要裡就有另一支影片的完整標題，摘要分數 0.83、
標題分數 0.00），拿它當依據會把整點新聞彙整接成長輩看到的那一則。

**命中率**：台別已知時，35 組真實標題中 15 組找得到（台別不明時 9 組），且一筆都沒接錯（實測見
`scripts/tv_news_article_eval.py`）。其餘 20 組是搜尋結果裡根本沒有該台的
那則報導——電視台不是每則新聞都上網，那是這個做法的上限，不是門檻的問題。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Optional, Protocol, Sequence
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# 十三家電視台的新聞網站。值是網域後綴，比對時只看「結尾是不是它」，
# 所以 news.tvbs.com.tw 這種子網域一併涵蓋。
#
# 清單與 CARE-n8n 影像節點認得的台別一致（`mutimedia process.json` 的
# TV_CHANNELS）：那邊認不出來的台別根本不會下送，這邊多列也用不到。
CHANNEL_DOMAINS: dict[str, tuple[str, ...]] = {
    "台視": ("ttv.com.tw",),
    "中視": ("ctv.com.tw",),
    "華視": ("cts.com.tw",),
    "民視": ("ftvnews.com.tw", "ftv.com.tw"),
    "公視": ("pts.org.tw",),
    "TVBS": ("tvbs.com.tw",),
    "三立": ("setn.com",),
    "東森": ("ebc.net.tw", "ettoday.net"),
    "中天": ("ctinews.com", "ctitv.com.tw"),
    "年代": ("eracom.com.tw",),
    "壹電視": ("nexttv.com.tw",),
    "非凡": ("ustv.com.tw",),
    "寰宇": ("globalnewstv.com.tw",),
    # 以下兩台不在 n8n 的台別清單裡，所以永遠不會是「已知台別」那條路；列在這裡
    # 是為了台別不明時，它們的網站也算得上「電視台自己的報導」。
    "鏡新聞": ("mnews.tw",),
    "八大": ("gtv.com.tw",),
}

# 標題比對前要丟掉的東西：電視標題用的分隔符（‧·．.、!?）、引號、空白，
# 以及網站標題常見的「｜中視新聞網」這類後綴分隔符。留著它們會讓同一句話
# 因為標點寫法不同而比不出來。
_NOISE_RE = re.compile(r"[\s　!！?？.,，、。：:；;\"'“”‘’「」『』（）()\[\]【】\-—–_|｜/／‧·・．]+")

_QUERY_NOISE_RE = re.compile(r"[\"'“”‘’「」『』]+")


# 該台自己上傳的影片。搜尋結果只有標題與網址、沒有頻道名稱，所以用標題裡的
# 台名當歸屬判準——電視台上傳時幾乎都會在標題帶上自己的名字（實測命中的影片
# 全部都有）。這條規則把命中率從 6/35 拉到 10/35。
_YOUTUBE_DOMAINS = ("youtube.com", "youtu.be")

CHANNEL_ALIASES: dict[str, tuple[str, ...]] = {
    "台視": ("台視",),
    "中視": ("中視",),
    "華視": ("華視",),
    "民視": ("民視",),
    "公視": ("公視", "公共電視"),
    "TVBS": ("TVBS",),
    "三立": ("三立", "SETN"),
    "東森": ("東森", "ETtoday"),
    "中天": ("中天",),
    "年代": ("年代",),
    "壹電視": ("壹電視", "壹起"),
    "非凡": ("非凡",),
    "寰宇": ("寰宇",),
    "鏡新聞": ("鏡新聞",),
    "八大": ("八大",),
}


@dataclass(frozen=True)
class TvNewsArticle:
    """找到的原始報導。`score` 是標題吻合度，留著是為了讓 log 看得出邊界情況。

    `is_video` 決定按鈕要寫「新聞原文」還是「新聞影片」——點下去是影片卻寫
    原文，使用者會以為自己點錯。
    """

    title: str
    url: str
    score: float
    is_video: bool = False


class SearchClient(Protocol):
    async def search(
        self, query: str, *, limit: int = 5, include_domains: Sequence[str] | None = None
    ) -> list[Any]: ...


def normalize(text: str) -> str:
    return _NOISE_RE.sub("", text or "")


def title_match_score(headline: str, title: str) -> float:
    """螢幕標題有多少比例出現在文章標題裡（0–1）。

    用「涵蓋率」而不是 `SequenceMatcher.ratio()`：網站標題往往比螢幕標題長
    （多了副標、台名後綴），兩邊長度差很多時 ratio 會被稀釋，同一則新聞也只
    拿到 0.6 上下。這裡要問的是「螢幕上那句話是不是都在文章標題裡」，分母
    因此固定是螢幕標題。
    """
    left, right = normalize(headline), normalize(title)
    if not left or not right:
        return 0.0
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(left)


def domain_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _host_matches(host: str, domains: Sequence[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_channel_domain(url: str, channel: str) -> bool:
    domains = CHANNEL_DOMAINS.get(channel)
    if not domains:
        return False
    return _host_matches(domain_of(url), domains)


def is_channel_video(url: str, title: str, channel: str) -> bool:
    """該台自己上傳的 YouTube 影片：網域是 YouTube 且標題裡有台名。"""
    if not _host_matches(domain_of(url), _YOUTUBE_DOMAINS):
        return False
    aliases = CHANNEL_ALIASES.get(channel, ())
    lowered = (title or "").lower()
    return any(alias.lower() in lowered for alias in aliases)


def build_query(headline: str, channel: str) -> str:
    """搜尋字串：台名＋標題。引號要拿掉，它在搜尋引擎裡是「完全相符」運算子。"""
    return f"{channel} {_QUERY_NOISE_RE.sub(' ', headline)}".strip()


class TvNewsArticleFinder:
    """`MATCH_THRESHOLD` 的由來見 `scripts/tv_news_article_eval.py` 的實測表。"""

    # 2026-09-18 實測（35 組真實螢幕標題 × 前五筆搜尋結果，逐筆人工判讀是不是
    # 長輩看到的那一則，資料在 evals/tv_news/article_lookup.json）：
    #
    #   門檻   找得到    接錯
    #   0.75   10/35     0
    #   0.60   13/35     0
    #   0.50   15/35     0     ← 採用
    #   0.25 以下開始收進接錯的
    #
    # **螢幕標題與網站標題常常是兩套寫法**，所以不能用高門檻：
    # 「維他命添色素‧影響智力」的網站版是「維他命摻色素 專家：恐過敏傷智力」
    # （0.70）、「成人健檢送800點…」的網站版是「衛福部推健康幣10月上路…」
    # （0.63），兩則都是同一則報導。真正接錯的（別集節目、標籤頁、同主題的
    # 另一篇）最高只有 0.25，所以 0.50 兩邊都留了餘裕。
    #
    # 不再往下調：0.25～0.33 之間真假混在一起（東森奇亞籽 0.33 是真的、
    # 東森另一篇 0.25 是假的），靠標題分不開。
    MATCH_THRESHOLD = 0.50
    # 台別不明時的門檻。比對的對象從「那一台」放寬成「任何一台」，誤接的空間
    # 因此變大，門檻要跟著收緊。
    #
    # 2026-09-18 實測（同一批 35 組標題，查詢字串不帶台名，結果在
    # evals/tv_news/article_lookup_no_channel.json）：
    #
    #   門檻   找得到    接到別台
    #   0.80    9/35      0
    #   0.70    9/35      0     ← 採用
    #   0.60   10/35      1（公視的「調節血糖.血脂健康食品」接成東森的報導）
    #
    # 0.70 與 0.80 找得到的數量一樣，取低的那個留一點餘裕給改寫過的標題。
    UNKNOWN_CHANNEL_THRESHOLD = 0.70
    SEARCH_LIMIT = 5

    def __init__(
        self,
        search_client: SearchClient | None,
        *,
        threshold: float | None = None,
        unknown_channel_threshold: float | None = None,
    ) -> None:
        self._search = search_client
        self._threshold = self.MATCH_THRESHOLD if threshold is None else threshold
        self._unknown_threshold = (
            self.UNKNOWN_CHANNEL_THRESHOLD
            if unknown_channel_threshold is None
            else unknown_channel_threshold
        )

    def _owner(self, url: str, title: str) -> str | None:
        """這筆是哪一台自己的東西（網站或官方影片）；都不是回 None。"""
        for channel in CHANNEL_DOMAINS:
            if is_channel_domain(url, channel) or is_channel_video(url, title, channel):
                return channel
        return None

    async def find(self, headline: str, channel: str) -> Optional[TvNewsArticle]:
        """找不到或搜尋失敗都回 None——這是加值，不能擋住查核。

        台別已知時只認那一台，查詢字串帶上台名；台別不明時認任何一台，查詢
        只有標題、門檻改用 `UNKNOWN_CHANNEL_THRESHOLD`。
        """
        if not self._search or not headline:
            return None
        known = channel in CHANNEL_DOMAINS
        # 不限定網域搜一次就好：限定網域的結果是這一份的子集（實測自家命中都
        # 在前五），多搜一次只是多花一次額度。
        query = build_query(headline, channel) if known else build_query(headline, "")
        threshold = self._threshold if known else self._unknown_threshold
        try:
            hits = await self._search(query, limit=self.SEARCH_LIMIT)
        except Exception as exc:  # noqa: BLE001 - 搜不到就不附連結，不影響判定卡
            logger.warning("stage=tv_news_article 搜尋失敗：%s", type(exc).__name__)
            return None

        best: Optional[TvNewsArticle] = None
        for hit in hits or []:
            url = getattr(hit, "url", "") or ""
            title = getattr(hit, "title", "") or ""
            if known:
                owner = channel if (
                    is_channel_domain(url, channel) or is_channel_video(url, title, channel)
                ) else None
            else:
                owner = self._owner(url, title)
            if owner is None:
                continue
            is_video = is_channel_video(url, title, owner)
            score = title_match_score(headline, title)
            if score < threshold:
                continue
            # 同分時文章優先於影片：長輩在 LINE 裡開文章比開影片省流量，也讀得快。
            better = best is None or score > best.score or (
                score == best.score and best.is_video and not is_video
            )
            if better:
                best = TvNewsArticle(
                    title=title, url=url, score=score, is_video=is_video
                )
        log_score = round(best.score, 3) if best else None
        logger.info(
            "stage=tv_news_article channel=%s hits=%d matched=%s score=%s",
            channel or "-",
            len(hits or []),
            bool(best),
            log_score,
        )
        return best
