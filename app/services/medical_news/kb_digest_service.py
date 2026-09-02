"""Tier 2 選材：從既有知識庫挑近期的衛教文章。

沒有命中使用者用藥時的保底內容。來源是 CARE-data 每日 ETL 維護的
`health_articles_chunks`——同一個 MongoDB，不新增任何外部依賴，也不消耗搜尋
與 LLM 額度。

這批語料本來就是為長輩寫的衛教與闢謠文章，正好是 Tier 2 要的東西；Tier 1 需要
的回收與安全警訊則不在其中（該語料沒有回收公告來源），所以兩層走不同的路。
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from app.services.medical_news import relevance

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
    def __init__(self, *, collection: Any, max_age_days: int) -> None:
        self._collection = collection
        self._max_age_days = max_age_days

    async def recent_articles(self, today: str, limit: int) -> list[KbArticle]:
        """近期的衛教文章，依發布日遞減。

        **只查 `chunk_index == 1`。** 這一個條件同時解掉兩件事：一篇文章天然只
        回一筆（不必把全部 chunk 撈進記憶體再依 url 收斂），而摘錄天然來自第一
        段——中段的 chunk 是切出來的片段，單獨呈現常常是半句話。

        日期過濾放在 Python 而不是查詢條件裡：這批語料的 `published_at` 同時
        存在西元與民國兩種格式（TFC 用西元、衛福部頁面常見民國），字串比大小
        會把民國年的全部排到最前面。`relevance.is_recent` 兩種都認得。
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
        for doc in docs:
            article = self._to_article(doc, today)
            if article is None or article.url in seen_urls:
                continue
            seen_urls.add(article.url)
            articles.append(article)
            if len(articles) >= limit:
                break
        return articles

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
