"""Tier 2 選材：從既有知識庫挑近期、對高齡讀者有用的衛教文章。

沒有命中使用者用藥時的保底內容。來源是 CARE-data 每日 ETL 維護的
`health_articles_chunks`——同一個 MongoDB，不新增任何外部依賴。

這批語料的四個來源裡，只有台灣事實查核中心與食藥署闢謠專區整批都是查核與
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
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from app.services.medical_news import relevance
from app.services.medical_news.article_grader import KbArticleGrader

logger = logging.getLogger(__name__)

# 每則卡片上的摘錄長度。超過的部分在 Flex 上會被截斷，先在這裡收斂比較誠實。
_EXCERPT_CHARS = 120

# 一次多撈幾倍，讓 Python 端的日期過濾與去重仍有足夠候選。
_OVERFETCH_FACTOR = 5


class KbArticle(NamedTuple):
    url: str
    title: str
    source_name: str
    published_at: str
    excerpt: str


class KbDigestService:
    def __init__(
        self,
        *,
        collection: Any,
        max_age_days: int,
        grader: KbArticleGrader | None = None,
        max_grade_calls: int = 0,
    ) -> None:
        self._collection = collection
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
        return articles

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
