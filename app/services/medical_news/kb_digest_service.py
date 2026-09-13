"""Tier 2 選材：從既有知識庫挑近期、對高齡讀者有用的衛教文章。

沒有命中使用者用藥時的保底內容。來源是 CARE-data 每日 ETL 維護的
`health_articles_chunks`——同一個 MongoDB，不新增任何外部依賴。

這批語料最早的四個來源裡（2026-09-09 起另有衛福部真相說明拆出的三個），只有台灣事實查核中心與食藥署闢謠專區整批都是查核與
衛教內容；**國健署新聞那批（`chunk_index=1` 且有網址的 1,013 筆，佔最新
文章的多數；2026-09-09 之前誤標為「衛福部闢謠網站」）名為闢謠，實際混了大量
政策新聞稿**——活動開幕、頒獎典禮、國際
交流、補助加碼、法規修正草案。Tier 1 需要的回收與安全警訊則四個來源都沒有
（該語料沒有回收公告來源），所以兩層走不同的路。

因此本模組有兩道內容過濾（都只作用於推播，不影響 RAG 檢索使用同一批語料）：

1. `relevance.is_policy_announcement`——標題黑名單，不花額度，先擋掉一望即知
   的活動與政績新聞稿。
2. `KbArticleGrader`（可選，`MEDICAL_NEWS_TIER2_GRADER_ENABLED`）——擋黑名單
   擋不掉的那種：標題像衛教、內容也是真衛教，但對象不是高齡讀者。

成本刻意是 O(每日候選數) 而不是 O(使用者數)：選材一天只跑一次，池子全體共用。

**第二個來源：健康媒體（`daily_health_news`，CARE-data `scraper_media` 寫入）。**
官方來源撐不起「每天都有當天的」：以今天或昨天發布為準，30 天裡有 10 天一篇新的
都沒有。媒體只在那種日子補位——選材分三群，官方新 > 媒體新 > 官方存量（見
`recent_articles`）。媒體刻意存在另一個 collection：`health_articles_chunks` 同時是
RAG 的檢索範圍，而檢索沒有依來源過濾，寫進去就會變成闢謠的引用來源。媒體的過濾
是分類允許清單加標題防線（`relevance.is_allowed_media_article`），不經過 grader。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, NamedTuple

from app.services.medical_news import relevance
from app.services.medical_news.article_grader import KbArticleGrader

logger = logging.getLogger(__name__)

# 每則卡片上的摘錄長度。超過的部分在 Flex 上會被截斷，先在這裡收斂比較誠實。
_EXCERPT_CHARS = 120

# 一次多撈幾倍，讓 Python 端的日期過濾與去重仍有足夠候選。
_OVERFETCH_FACTOR = 5

# 「新」的定義：今天或昨天發布。不是只認今天，因為時序是 CARE-data ETL 在台北
# 08:00（GitHub Actions cron `0 0 * * *` UTC）、推播在台北 09:00
# （MEDICAL_NEWS_PUSH_TIME）——昨天下午發布的文章今天早上才進庫，對使用者而言
# 它就是「今天第一次看到」。官方來源的 published_at 只有日期沒有時刻，也無法
# 切得比「一天」更細。
#
# 量測（2026-09-09，知識庫有網址的官方與 TFC 文章，回推 30 天）：以這個窗口，
# 30 天裡有 10 天官方一篇新的都沒有——那十天就是媒體補位的日子。
FRESH_DAYS = 1

# 選材優先序：官方新 > 媒體新 > 官方存量。見 `recent_articles`。
PRIORITY_OFFICIAL_FRESH = 0
PRIORITY_MEDIA_FRESH = 1
PRIORITY_OFFICIAL_STOCK = 2


class KbArticle(NamedTuple):
    url: str
    title: str
    source_name: str
    published_at: str
    excerpt: str
    # 選材優先序，數字小的先推。見 `recent_articles` 的「官方優先、媒體補位」。
    # 預設 0：不分群時所有文章同一群，與加入這個欄位之前的行為完全相同。
    priority: int = 0


class KbDigestService:
    def __init__(
        self,
        *,
        collection: Any,
        max_age_days: int,
        grader: KbArticleGrader | None = None,
        max_grade_calls: int = 0,
        media_collection: Any | None = None,
    ) -> None:
        self._collection = collection
        # 健康媒體（daily_health_news）。None 時池子只有官方，與加入媒體之前完全相同。
        self._media_collection = media_collection
        self._max_age_days = max_age_days
        # grader 為 None 時只剩標題黑名單那一道。這是刻意保留的降級：Firecrawl
        # 缺席時 Tier 1 整個不存在（見 dependencies），Tier 2 不該跟著消失。
        self._grader = grader
        self._max_grade_calls = max_grade_calls

    async def recent_articles(self, today: str, limit: int) -> list[KbArticle]:
        """近期的衛教文章，依發布日遞減。

        **只查 `chunk_index == 1`。** 這一個條件同時解掉兩件事：一篇文章天然只
        回一筆（不必把全部 chunk 撈進記憶體再依 url 收斂），而摘錄天然來自第一
        段——中段的 chunk 是切出來的片段，單獨呈現常常是半句話。

        日期過濾放在 Python 而不是查詢條件裡：`published_at` 是字串欄位，而
        `_parse_date` 要處理的不只是格式，還有佔位字串（「不詳」「未提供」）與
        未來日期的容忍度，那些都不是 Mongo 的比較運算子表達得出來的。

        （**訂正 2026-09-04**：本段原本的理由是「這批語料同時存在西元與民國兩種
        格式，字串比大小會把民國年排到最前面」。那是未經查證的推測——實際量測
        2,422 筆，民國格式 0 筆，全部是西元 `YYYY-MM-DD`。既然如此，
        `.sort("published_at", -1)` 這個字串排序也就等同於時序排序，可以留在
        查詢端。`relevance._parse_date` 對民國年的支援仍然保留，理由見該處。）
        """
        cursor = (
            self._collection.find(
                {
                    "chunk_index": 1,
                    "url": {"$nin": [None, ""]},
                }
            )
            .sort("published_at", -1)
            .limit(max(limit, 1) * _OVERFETCH_FACTOR)
        )
        docs = await cursor.to_list(length=None)

        articles: list[KbArticle] = []
        seen_urls: set[str] = set()
        graded = 0
        rejected = 0
        errors = 0
        for doc in docs:
            article = self._to_article(doc, today)
            if article is None or article.url in seen_urls:
                continue
            seen_urls.add(article.url)

            if self._grader is not None:
                if graded >= self._max_grade_calls:
                    # 預算用完就停止選材，不是「剩下的一律放行」。放行等於在
                    # 額度吃緊的那天悄悄關掉這道防線——而那正是最需要它的時候。
                    logger.info(
                        "tier2_grade_budget_exhausted graded=%d limit=%d",
                        graded,
                        self._max_grade_calls,
                    )
                    break
                graded += 1
                verdict = await self._is_useful(article)
                if verdict is None:
                    errors += 1
                    continue
                if not verdict:
                    rejected += 1
                    continue

            articles.append(article)
            if len(articles) >= limit:
                break

        if self._grader is not None:
            # 這一行是 Tier 2 唯一的可觀測性。grader 若整個壞掉（配額耗盡、
            # 金鑰過期），每篇都會被 fail closed 掉，池子變空，而空池子的既有
            # 行為是「安靜地不推」——沒有這行 log，全體使用者收不到 Tier 2 這件
            # 事在系統外觀上完全健康。errors 非零時提到 error 級別。
            log = logger.error if errors else logger.info
            log(
                "tier2_pool picked=%d graded=%d rejected=%d grader_errors=%d",
                len(articles),
                graded,
                rejected,
                errors,
            )
        # 官方優先、媒體補位。三群依序：
        #   0 官方新（今天或昨天）——疫情、回收這類有時效的官方消息永遠排最前
        #   1 媒體新——官方當天沒有新東西時，才輪到媒體
        #   2 官方存量（30 天內）——連媒體都沒有時的最後保底
        #
        # 為什麼不單純依日期混排：元氣網一天約 15.8 篇（2026-08-24～09-13 週
        # sitemap 364 篇 / 21 天），官方約 2 篇，混排時池子幾乎全是媒體——這與
        # 2026-09-04 量到「衛福部那批佔滿選材窗口 33/50」是同一個失效形狀。
        #
        # 媒體新排在官方存量之前，是因為每日推播要的就是「今天的」；存量衛教
        # 不會過期，但也不是新聞。分群本身由 push_scheduler._pick_tier2 承擔：
        # 它先挑完前一群才看下一群，群內才做 user_id 錯開。
        fresh, stock = [], []
        for article in articles:
            if relevance.is_recent(article.published_at, today, FRESH_DAYS):
                fresh.append(article._replace(priority=PRIORITY_OFFICIAL_FRESH))
            else:
                stock.append(article._replace(priority=PRIORITY_OFFICIAL_STOCK))
        media = await self._media_articles(today, limit)
        return fresh + media + stock

    async def _media_articles(self, today: str, limit: int) -> list[KbArticle]:
        """健康媒體的當日新文章，已套用分類允許清單與標題防線。

        不經過 grader：grader 的判準是「對高齡讀者有沒有用」，已經被否決過
        （使用者不只長輩）；媒體要擋的是八卦、兇殺、理財這類與健康無關的東西，
        那是分類與標題就判得出來的事，不需要花模型額度。

        `published_at` 的字串比較在這裡是成立的：這個 collection 只有
        CARE-data `scraper_media` 一個寫入者，它保證格式是 `YYYY-MM-DD`
        （台北日期）。官方那個 collection 不能這樣做，因為 DataAction 那批是
        `YYYY/MM/DD`。
        """
        if self._media_collection is None:
            return []
        since = (date.fromisoformat(today) - timedelta(days=FRESH_DAYS)).isoformat()
        cursor = (
            self._media_collection.find({"published_at": {"$gte": since}})
            .sort("published_at", -1)
            .limit(max(limit, 1) * _OVERFETCH_FACTOR)
        )
        docs = await cursor.to_list(length=None)

        picked: list[KbArticle] = []
        seen: set[str] = set()
        rejected = 0
        for doc in docs:
            url = (doc.get("url") or "").strip()
            title = (doc.get("title") or "").strip()
            if not url or not title or url in seen:
                continue
            seen.add(url)
            if not relevance.is_allowed_media_article(doc.get("category"), title):
                rejected += 1
                continue
            published_at = doc.get("published_at")
            if not relevance.is_recent(published_at, today, FRESH_DAYS):
                continue
            picked.append(
                KbArticle(
                    url=url,
                    title=title,
                    source_name=(doc.get("source_name") or "").strip() or "健康媒體",
                    published_at=str(published_at),
                    excerpt=(doc.get("excerpt") or "").strip()[:_EXCERPT_CHARS],
                    priority=PRIORITY_MEDIA_FRESH,
                )
            )
            if len(picked) >= limit:
                break

        # 與 tier2_pool 那行同一個用途：媒體若整批被擋（站方改分類名稱會讓允許
        # 清單全部落空），池子只會安靜地少一群。這行讓「今天怎麼都是官方舊文」
        # 一 grep 就知道原因。
        logger.info(
            "tier2_media picked=%d rejected=%d scanned=%d", len(picked), rejected, len(docs)
        )
        return picked

    async def _is_useful(self, article: KbArticle) -> bool | None:
        """grader 的判定；`None` 代表判定沒有發生（與判定為 False 不同）。

        兩者在呼叫端都導致這篇被丟棄（fail closed，沿用 design.md 決策 4：
        主動推播沒有人在等，沒推遠比推錯好），但只有前者該被記成錯誤。
        """
        assert self._grader is not None
        try:
            judgement = await self._grader.judge_article(
                article.title, article.excerpt
            )
        except Exception:
            logger.exception(
                "tier2 article grading failed; excluding url=%s", article.url
            )
            return None
        if not judgement.is_useful_for_elderly:
            logger.info(
                "tier2_reject url=%s reason=%s", article.url, judgement.reason
            )
        return judgement.is_useful_for_elderly

    def _to_article(self, doc: dict, today: str) -> KbArticle | None:
        url = (doc.get("url") or "").strip()
        if not url:
            # 食藥署 DataAction feed 結構上不提供文章網址。消息卡必須有可點的
            # 來源連結，分享卡尤其——那是收件人唯一能自行查證的東西。
            return None

        title = (doc.get("original_title") or "").strip()
        if not title:
            # 標題是卡片上唯一必然顯示的東西，缺了就是一張空白卡。
            return None

        if relevance.is_policy_announcement(title):
            # 政策宣傳與活動新聞稿。擋在 grader 之前是為了省額度：這一道不花錢，
            # 而它擋掉的量不小（量測見 POLICY_ANNOUNCEMENT_KEYWORDS）。
            return None

        published_at = doc.get("published_at")
        if not relevance.is_recent(published_at, today, self._max_age_days):
            return None

        excerpt = (doc.get("chunk_content") or "").strip()[:_EXCERPT_CHARS]
        return KbArticle(
            url=url,
            title=title,
            source_name=(doc.get("source_name") or "").strip() or "政府公開資訊",
            published_at=str(published_at),
            excerpt=excerpt,
        )
