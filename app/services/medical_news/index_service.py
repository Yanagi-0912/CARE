"""每日為「全體使用者正在服用的每一種藥」索引近期官方消息。

這支排程**與使用者無關**：快取的鍵是藥名，不是使用者。因為來源鎖死官方域，
同一個藥名搜出來的結果跟誰在吃完全無關，所以成本是 O(不重複藥數) 而不是
O(使用者數)（design.md 決策 2）。

與推播排程分開的第二個理由比成本更重要：政府站台逾時是常態。合在一起時搜尋
失敗會讓整輪推播停擺；分開之後索引失敗只是「今天沒有新內容」，推播照常拿昨天
的索引跑。
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, NamedTuple, Optional

from app.models.medical_news import DrugNews
from app.repositories.medical_news_repository import DrugNewsRepository
from app.repositories.medication_repository import MedicationRepository
from app.services.medical_news import relevance
from app.services.rag.web_client import ScrapedPage, WebSearchHit
from app.services.rag.whitelist import is_allowed_url, with_whitelist_site_filter

logger = logging.getLogger(__name__)

# 搜尋語句。刻意窄：這支排程找的是「這個藥出了什麼事」，不是一般衛教——
# 一般衛教由 Tier 2 從既有知識庫供應，不需要動用搜尋額度。
_QUERY_TEMPLATE = "{drug} 藥品 回收 OR 安全資訊 OR 警訊"

# 從內文抽發布日的啟發式規則。gov.tw 各頁面的日期位置不一致，這個抽取**不可靠**
# 是已知的（design.md 證據缺口 2）；因此抽不到時的預設是排除而非放行——
# 「不知道多舊」的消息不該混進「近期警訊」。
_DATE_IN_TEXT = re.compile(r"(\d{2,4})[-/年](\d{1,2})[-/月](\d{1,2})")

_MAX_TEXT_CHARS = 8000


class IndexRunResult(NamedTuple):
    """一輪索引的結果。

    存在的理由不只是 log：Tier 1 的實際命中率目前完全未知（design.md 證據缺口
    3），而這幾個數字是唯一能回答那個問題的東西。
    """

    keys_scanned: int
    hits_fetched: int
    stored: int
    skipped: int


class DrugNewsIndexService:
    def __init__(
        self,
        *,
        web_client: Any,
        grader: Any,
        repository: Any = DrugNewsRepository,
        medication_repository: Any = MedicationRepository,
        max_age_days: int,
        search_limit: int,
    ) -> None:
        self._web_client = web_client
        self._grader = grader
        self._repository = repository
        self._medication_repository = medication_repository
        self._max_age_days = max_age_days
        self._search_limit = search_limit

    async def run_once(self, today: str) -> IndexRunResult:
        drug_keys = await self._medication_repository.list_active_drug_keys(today)
        hits_fetched = 0
        stored = 0
        skipped = 0
        # 同一輪內的抓取快取。同一則公告常同時提到多種藥，逐藥重抓是白費的
        # 請求，對政府主機也是不必要的負載。判定仍逐藥進行——判定是針對藥的。
        page_cache: dict[str, Optional[ScrapedPage]] = {}

        for drug_key in drug_keys:
            try:
                hits = await self._search(drug_key)
            except Exception:
                logger.exception(
                    "[DrugNewsIndex] 搜尋失敗，跳過此藥：%s", drug_key
                )
                skipped += 1
                continue

            for hit in hits:
                hits_fetched += 1
                try:
                    if await self._index_hit(drug_key, hit, today, page_cache):
                        stored += 1
                    else:
                        skipped += 1
                except Exception:
                    # 判定或抓取失敗一律 fail closed：不存、不推，只跳過這一筆。
                    # 與 rag-crag 的「失敗仍照常生成」刻意相反，理由見 design.md
                    # 決策 4——主動推播沒有人在等，沒推遠比推錯好。
                    logger.exception(
                        "[DrugNewsIndex] 判定失敗，跳過：%s / %s", drug_key, hit.url
                    )
                    skipped += 1

        logger.info(
            "[DrugNewsIndex] keys=%d hits=%d stored=%d skipped=%d",
            len(drug_keys),
            hits_fetched,
            stored,
            skipped,
        )
        return IndexRunResult(len(drug_keys), hits_fetched, stored, skipped)

    async def _search(self, drug_key: str) -> list[WebSearchHit]:
        query = with_whitelist_site_filter(_QUERY_TEMPLATE.format(drug=drug_key))
        return await self._web_client.search(query, limit=self._search_limit)

    async def _index_hit(
        self,
        drug_key: str,
        hit: WebSearchHit,
        today: str,
        page_cache: dict[str, Optional[ScrapedPage]],
    ) -> bool:
        """單筆搜尋結果的四道防線。回傳是否寫入。

        順序是刻意的，由便宜到昂貴：白名單 → 字面比對 → 抓取 → 模型判定。
        調換之後功能仍正常，只是每天多花本來不必花的錢，所以由測試鎖住。
        """
        # 防線 1：非官方域直接丟棄，連抓取都不做。
        if not hit.url or not is_allowed_url(hit.url):
            return False

        # 防線 2：字面比對先於模型。這是為 recall 而設的成本篩選——它擋掉的是
        # 「與這個藥完全無關」的結果；「是不是真的在講這個藥」由防線 4 回答。
        if not relevance.mentions_drug(f"{hit.title} {hit.description}", drug_key):
            return False

        page = await self._fetch(hit.url, page_cache)
        if page is None:
            return False

        text = (page.text or "")[:_MAX_TEXT_CHARS]

        # 防線 3：時效。抽不到日期即排除——gov.tw 的日期抽取不可靠是已知的，
        # 缺席時的預設必須是排除，否則「不知道多舊」會混進「近期警訊」。
        published_at = self._extract_published_at(page, hit)
        if not relevance.has_usable_date(published_at):
            return False
        if not relevance.is_recent(published_at, today, self._max_age_days):
            return False

        # 防線 4：模型判定。例外往上拋，由 run_once 接住並 fail closed。
        judgement = await self._grader.judge(drug_key, hit.title, text)
        if not judgement.is_about_this_drug or judgement.concern_kind == "none":
            return False

        # 輸出防線：踩到用藥建議紅線即整則丟棄，**不改寫**。改寫等於讓模型
        # 再賭一次，而這道防線存在的前提正是不能相信模型會自己守住。
        if relevance.violates_output_guard(judgement.summary):
            logger.warning(
                "[DrugNewsIndex] 摘要含用藥建議，整則丟棄：%s", hit.url
            )
            return False

        news = DrugNews(
            drug_key=drug_key,
            key_kind=_key_kind(drug_key),
            url=hit.url,
            title=hit.title,
            source_name=_source_name(hit.url),
            published_at=published_at,
            summary=judgement.summary,
            concern_kind=judgement.concern_kind,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:32],
        )
        await self._repository.upsert_by_url(news)
        return True

    async def _fetch(
        self, url: str, page_cache: dict[str, Optional[ScrapedPage]]
    ) -> Optional[ScrapedPage]:
        if url in page_cache:
            return page_cache[url]
        try:
            page = await self._web_client.scrape_page(url)
        except Exception:
            logger.warning("[DrugNewsIndex] 抓取失敗：%s", url)
            page = None
        page_cache[url] = page
        return page

    @staticmethod
    def _extract_published_at(page: ScrapedPage, hit: WebSearchHit) -> Optional[str]:
        """從內文抽出發布日。抽不到回 None。

        **這個抽取不可靠是已知的**（design.md 證據缺口 2）：gov.tw 各頁面的日期
        位置不一致，且頁面上常有多個日期（發布日、修改日、有效期限）。取第一個
        符合格式的，是在沒有更好依據前的權宜；抽錯的風險由呼叫端的「未來日期
        一律排除」與 `max_age_days` 兩道一起承擔。
        """
        match = _DATE_IN_TEXT.search(page.text or "")
        if match is None:
            return None
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"


def _key_kind(drug_key: str) -> str:
    """成分名一律是拉丁字母，中文品名不是。這個區分只影響呈現與統計。"""
    return "ingredient" if drug_key.isascii() else "name_zh"


def _source_name(url: str) -> str:
    """從網域推出可讀的來源名。

    只認已知的官方網域——白名單以外的 URL 根本到不了這裡，因此不需要通用規則。
    """
    for domain, name in (
        ("fda.gov.tw", "食藥署"),
        ("hpa.gov.tw", "國民健康署"),
        ("cdc.gov.tw", "疾管署"),
        ("mohw.gov.tw", "衛生福利部"),
        ("nhi.gov.tw", "健保署"),
    ):
        if domain in url:
            return name
    return "政府公開資訊"
