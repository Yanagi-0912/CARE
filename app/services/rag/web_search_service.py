import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain_core.documents import Document

from app.core.rag_sources import SourceRef, set_request_rag_sources
from app.core.request_logging import stage_timer
from app.core.request_context import get_line_user_id
from app.services.gemini import GeminiService
from app.i18n.messages import t
from app.services.rag.cannot_answer import (
    CANNOT_ANSWER_MARKERS,
    NO_ANSWER_SENTINEL,
    answer_preview,
    matched_cannot_answer_marker,
)
from app.services.rag.answer_prompts import build_web_prompt, wrap_context
from app.services.rag.fail_messages import (
    NO_ANSWER_MESSAGE,
    RagFailCode,
    rag_fail,
)
from app.services.rag.link_check import LinkChecker, dead_urls
from app.services.rag.query_rewriter import RewrittenQuery
from app.services.rag.web_client import WebSearchClient, WebSearchUnavailable
from app.services.gemini.shared.parser import content_to_text
from app.services.rag.whitelist import (
    is_allowed_url,
    normalize_url,
    with_whitelist_site_filter,
)

logger = logging.getLogger(__name__)

CITE_TOP_K = 3
# 相容舊測試／匯入：預設繁中文案
WEB_ANSWER_PREFIX = "以下參考網路公開資料"
WEB_SEARCH_LIMIT = 8
WEB_PAGE_CHAR_LIMIT = 8000
# search snippet 達此長度就不打 scrape（避免 gov.tw 頁面常逾時）
WEB_SNIPPET_MIN_CHARS = 20

OnWebFallbackSuccess = Callable[..., Awaitable[Any]]


def web_answer_prefix(language: str | None = None) -> str:
    return t("rag.web_answer_prefix", language=language)


def _interleave(doc_lists: Sequence[list[Document]], *, limit: int) -> list[Document]:
    """各路輪流取一份、網址去重，取滿 *limit* 為止。

    交錯而不串接：串接的話中文那一路會吃滿全部名額，而罕見病正是中文那路
    搜到不相關內容（多發性硬化症、泌尿科問答）、英文那路才有正解的情況。
    名額維持 CITE_TOP_K 而不是兩路相加：來源清單只列前 CITE_TOP_K 份，
    多給生成的文件會被引用成清單上沒有的編號。
    """
    merged: list[Document] = []
    seen: set[str] = set()
    depth = max((len(docs) for docs in doc_lists), default=0)
    for rank in range(depth):
        for docs in doc_lists:
            if rank >= len(docs):
                continue
            doc = docs[rank]
            url = str(doc.metadata.get("url") or "")
            if url in seen:
                continue
            seen.add(url)
            merged.append(doc)
            if len(merged) >= limit:
                return merged
    return merged


class WebSearchService:
    def __init__(
        self,
        gemini_service: GeminiService,
        web_client: WebSearchClient | None = None,
        on_web_fallback_success: OnWebFallbackSuccess | None = None,
        link_checker: LinkChecker | None = None,
        en_search_domains: Sequence[str] = (),
    ) -> None:
        self.gemini_service = gemini_service
        self.web_client = web_client
        self._on_web_fallback_success = on_web_fallback_success
        # None＝不檢查來源網址存活，行為與導入前完全相同（見 link_check.py）
        self.link_checker = link_checker
        # 空＝不搜英文那一路（見 config.RAG_WEB_SEARCH_EN_DOMAINS）
        self._en_search_domains = tuple(
            domain.strip() for domain in en_search_domains if domain.strip()
        )

    async def answer(
        self, query: str, *, search_queries: RewrittenQuery | None = None
    ) -> str:
        """*search_queries* 只決定「拿什麼去搜」；生成與知識回報一律用原句。"""
        try:
            web_docs = await self._fetch_web_docs(query, search_queries)
        except WebSearchUnavailable as exc:
            # 「沒搜成」與「搜了沒有」分開回：WEB_EMPTY 的文案叫使用者換個說法，
            # 對限流與逾時完全沒用（理由見 web_client.WebSearchUnavailable）。
            code = (
                RagFailCode.WEB_RATE_LIMITED
                if exc.rate_limited
                else RagFailCode.WEB_ERROR
            )
            logger.warning(
                "rag_fail code=%s status=%s reason=%s", code, exc.status, exc.reason
            )
            return rag_fail(code)
        if not web_docs:
            logger.info("rag_fail code=%s", RagFailCode.WEB_EMPTY)
            return rag_fail(RagFailCode.WEB_EMPTY)

        web_answer = await self._generate_answer(query, web_docs)
        if self._is_cannot_answer(web_answer):
            marker = matched_cannot_answer_marker(web_answer, CANNOT_ANSWER_MARKERS)
            preview = answer_preview(web_answer)
            logger.info(
                "rag_fail code=%s matched_marker=%s answer_preview=%s",
                RagFailCode.MODEL_REFUSE,
                marker,
                preview,
            )
            return rag_fail(RagFailCode.MODEL_REFUSE)

        annotated = f"{web_answer_prefix()}\n\n{web_answer}"
        dead = await self._dead_source_urls(web_docs)
        result = self._append_sources(annotated, web_docs, dead)
        await self._maybe_create_knowledge_report(query, web_docs, dead)
        return result

    async def _dead_source_urls(self, docs: list[Document]) -> frozenset[str]:
        """判定哪些來源網址現在打不開。關閉或失敗時回空集合。

        網搜路徑的網址剛被 search／scrape 碰過，判死的比例本來就該遠低於
        知識庫路徑；留著這道檢查主要是為了擋下游的知識回報——見
        `_extract_source_urls`。
        """
        if self.link_checker is None:
            return frozenset()
        urls = [u for u in (self._doc_url(doc) for doc in docs) if u]
        if not urls:
            return frozenset()
        with stage_timer(logger, "rag_link_check", checked=len(urls)) as lc_timing:
            dead = await dead_urls(self.link_checker, urls)
            lc_timing["dead"] = len(dead)
        if dead:
            logger.info(
                "citation_link_dead path=web count=%d urls=%s", len(dead), sorted(dead)
            )
        return dead

    @staticmethod
    def _doc_url(doc: Document) -> str:
        return str(doc.metadata.get("url") or "").strip()

    async def _maybe_create_knowledge_report(
        self,
        query: str,
        web_docs: list[Document],
        dead_urls: frozenset[str] = frozenset(),
    ) -> None:
        if self._on_web_fallback_success is None:
            return

        urls = self._extract_source_urls(web_docs, dead_urls)
        if not urls:
            return

        line_user_id = get_line_user_id()
        if not line_user_id:
            logger.info(
                "web_fallback_skip_knowledge_report reason=missing_line_user_id"
            )
            return

        try:
            await self._on_web_fallback_success(
                question=query,
                urls=urls,
                line_user_id=line_user_id,
            )
        except Exception:
            logger.exception("web_fallback_knowledge_report_failed")

    @staticmethod
    def _extract_source_urls(
        docs: list[Document], dead_urls: frozenset[str] = frozenset()
    ) -> list[str]:
        """挑出要送進知識回報的網址。

        判死的網址在這裡就擋掉，不是只在顯示層擋。回報一旦被核准就會
        ingest 進向量庫，那個 url 會成為之後每一次引用它的死連結——把死鏈
        擋在入庫前，比事後在出口層一直降級它便宜得多。
        """
        urls: list[str] = []
        seen: set[str] = set()
        for doc in docs:
            if len(urls) >= CITE_TOP_K:
                break
            url = str(doc.metadata.get("url") or "").strip()
            if not url or url in seen or url in dead_urls:
                continue
            seen.add(url)
            urls.append(url)
        return urls

    async def _generate_answer(self, question: str, docs: list[Document]) -> str:
        context = "\n".join(
            f"{idx}. {doc.page_content}" for idx, doc in enumerate(docs, start=1)
        )
        messages = build_web_prompt().format_messages(
            question=question, context=wrap_context(context)
        )
        with stage_timer(logger, "rag_web_generate", docs=len(docs)):
            result = await self.gemini_service.chat_model.ainvoke(messages)
        # `content_to_text` 而非 `str()`：Gemini 開著 thinking 時 `.content`
        # 回的是 list-of-parts（`[{"type": "text", "text": "…", "extras":
        # {"signature": "<數千字 base64>"}}]`），`str()` 會把整個 Python
        # repr 連同簽章一起變成「答案」。實測一則 400 字的衛教回覆會被包成
        # 4,600~7,000 字，之後全程當作答案文字傳遞——進 agent 的 context、
        # 進引用解析、也會進卡片。
        # 空字串＝答不出來，理由同 RagAnswerService._generate_answer。
        answer_text = content_to_text(result.content) or NO_ANSWER_SENTINEL
        return answer_text

    async def _fetch_web_docs(
        self, query: str, search_queries: RewrittenQuery | None = None
    ) -> list[Document]:
        """中英兩路並行搜尋、交錯合併；兩路都沒有可用文件時以原句重搜一次。

        中文那一路查 gov.tw：有改寫時用 zh_terms，沒有時沿用原句（web tool
        與 CRAG 關閉時的行為因此與導入前相同）。英文那一路只在有 en_terms
        且設定了英文網域時才搜。

        重搜是因為 Firecrawl 會隨機回 0 筆：2026-09-12 同一查詢連打兩次，
        10 組裡有 2 組一次 0 筆、一次 5 筆。重搜用原句，因為改寫過的關鍵字
        不一定比原句好搜；沒有改寫時就是同一句再搜一次。只在完全沒有可用
        文件時才重搜，所以多花的時間只落在原本就會失敗的題目上。

        重搜只針對「搜了、真的 0 筆」。任一路是「沒搜成」（WebSearchUnavailable：
        429、逾時、5xx）且沒有任何一路拿到文件時，直接把失敗往上拋、不重搜——
        限流當下立刻再打一次只會再吃一次 429，逾時再等一次 15 秒也一樣。
        有一路拿到文件就照常用它，另一路的失敗只留在 stage log。
        """
        if self.web_client is None:
            return []
        zh_query = (search_queries.zh_terms if search_queries else "") or query
        en_query = (
            search_queries.en_terms
            if search_queries is not None and self._en_search_domains
            else ""
        )

        legs = [self._search_leg("zh", with_whitelist_site_filter(zh_query))]
        if en_query:
            # 英文那一路只取 CITE_TOP_K 筆：交錯合併後它最多用到 2 份，多搜的
            # 只是多等，而兩路並行時整段是被較慢的那一路拖住。2026-09-12 實測
            # v2 includeDomains（nih.gov、medlineplus.gov）limit 8 要 1.9-5.3 秒、
            # limit 3 是 1.0-1.6 秒（各 3 次）。
            legs.append(
                self._search_leg(
                    "en",
                    en_query,
                    include_domains=self._en_search_domains,
                    limit=CITE_TOP_K,
                )
            )
        outcomes = await asyncio.gather(*legs, return_exceptions=True)
        failures = [o for o in outcomes if isinstance(o, WebSearchUnavailable)]
        unexpected = [
            o
            for o in outcomes
            if isinstance(o, BaseException) and not isinstance(o, WebSearchUnavailable)
        ]
        if unexpected:
            # 只有搜尋服務的失敗會被轉成 WebSearchUnavailable；其他例外是程式錯誤，
            # 照 gather 原本的行為往上拋，不要包成「搜尋服務暫時無法使用」。
            raise unexpected[0]
        docs = _interleave(
            [o for o in outcomes if isinstance(o, list)], limit=CITE_TOP_K
        )
        if docs:
            return docs
        if failures:
            raise failures[0]
        return await self._search_leg("zh_retry", with_whitelist_site_filter(query))

    async def _search_leg(
        self,
        leg: str,
        query: str,
        *,
        include_domains: Sequence[str] | None = None,
        limit: int = WEB_SEARCH_LIMIT,
    ) -> list[Document]:
        assert self.web_client is not None
        with stage_timer(logger, "rag_web_search", leg=leg) as t_search:
            try:
                hits = await self.web_client.search(
                    query,
                    limit=limit,
                    include_domains=include_domains,
                )
            except WebSearchUnavailable as exc:
                # hits 記成 unavailable 而不是 0：以前記 error 後回空 list，
                # 從外面看與「搜到 0 筆」分不開，429 就這樣被當成找不到。
                t_search["hits"] = "unavailable"
                t_search["status"] = exc.status
                raise
            except Exception as exc:
                # 客戶端沒照契約丟 WebSearchUnavailable 的例外也是「沒搜成」，
                # 轉成同一種型別讓上層用同一條路處置。
                t_search["hits"] = "error"
                raise WebSearchUnavailable(type(exc).__name__) from exc
            t_search["hits"] = len(hits)

        # 整段的 ms 與次數要分開記：單次 scrape 不慢但跑了六次，與單次就卡滿
        # 逾時，是兩個不同的問題、兩種不同的修法。scrape 改並行之後這裡的 ms
        # 是「最慢的那一筆」而不是總和，要看總量得配 scrapes= 一起讀。
        with stage_timer(logger, "rag_web_scrape_loop", leg=leg) as t_loop:
            return await self._collect_web_docs(hits, t_loop)

    async def _collect_web_docs(
        self, hits: list[Any], t_loop: dict[str, Any]
    ) -> list[Document]:
        """把搜尋結果收成最多 CITE_TOP_K 份文件；缺內文的候選並行補抓。

        scrape 是並行而不是逐一 await 的：單次 scrape 的逾時是 45 秒，序列時
        n 筆抓不到就是 n×45 秒，整段網搜的 45 秒總逾時撐不到第二筆。並行之後
        這一段的牆鐘時間是「最慢的那一筆」而不是「全部相加」。併發總量由
        FirecrawlClient 的閘控制（firecrawl_client.DEFAULT_MAX_CONCURRENCY），
        這裡不再自己疊一層：兩層閘會讓「到底卡在哪一層」從 log 判讀不出來。
        """
        candidates = self._candidates(hits)
        # 只抓「會被採用的那幾筆」，也就是前 CITE_TOP_K 名。視窗設得比這更寬
        # 是實測驗證過的錯：2026-09-16 用 scripts/web_search_latency_bench.py
        # 跑 golden set 的 21 題網搜題，視窗設 CITE_TOP_K+2 時，有一題把排在
        # 第 4、5 名的 nhi.gov.tw PDF 也預抓了，那一筆卡滿 45 秒逾時——而前
        # 三名的 snippet 全都夠用，逐一 await 的舊版走到第三份就停、根本不會
        # 碰它。預抓把「可能用不到的補救」變成「一定付出的成本」。
        #
        # 視窗內抓不到時不再往後補抓：後面的候選幾乎都有夠長的 snippet
        # （同一輪實測 153 筆候選裡 151 筆 ≥ 20 字），下面的走訪會直接拿它們
        # 遞補，不必再花一次逾時。
        window = candidates[:CITE_TOP_K]
        # 優先用 search snippet，避免 Firecrawl scrape 15–45s 連逾時拖死整輪
        to_scrape = [
            url for url, _title, snippet in window
            if len(snippet) < WEB_SNIPPET_MIN_CHARS
        ]
        scraped_by_url = await self._scrape_all(to_scrape)

        docs: list[Document] = []
        for url, title, snippet in candidates:
            text = snippet
            if len(text) < WEB_SNIPPET_MIN_CHARS:
                # 抓不到就退回原本的短 snippet：短歸短，它仍是這個網址目前唯一
                # 的內容，丟掉只會讓這一筆連來源都排不進清單。
                text = scraped_by_url.get(url, "") or text
            if not text:
                continue
            docs.append(
                Document(
                    page_content=text[:WEB_PAGE_CHAR_LIMIT],
                    metadata={
                        "source_name": title or url,
                        "url": url,
                    },
                )
            )
            if len(docs) >= CITE_TOP_K:
                break
        t_loop["hits"] = len(hits)
        t_loop["scrapes"] = len(to_scrape)
        t_loop["docs"] = len(docs)
        return docs

    @staticmethod
    def _candidates(hits: list[Any]) -> list[tuple[str, str, str]]:
        """過白名單、去重、保序，回傳 (url, title, snippet)。

        去重在這裡一次做完，而不是像以前那樣等拿到內文才記進 seen：以前同一個
        網址若第一次抓不到內文就不會進 seen，後面再出現時會**再抓一次**，白白
        多花一次 scrape 去等同一個逾時。
        """
        out: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for hit in hits:
            raw_url = (hit.url or "").strip()
            if not raw_url:
                continue
            # 先正規化再比對／去重／顯示：hit URL 可能帶 utm、大小寫不一，
            # 也可能是 whitelist.py 判定會造成解析歧異的字串（None）。
            # normalize 是冪等的（whitelist.py 的不動點檢查保證），對已正規化
            # 的字串再 normalize 一次會拿回原字串，所以下面直接用 url 檢查
            # 允許清單，不需要對 raw_url 再算一次。
            url = normalize_url(raw_url)
            if url is None or url in seen or not is_allowed_url(url):
                continue
            seen.add(url)
            out.append(
                (url, (hit.title or "").strip(), (hit.description or "").strip())
            )
        return out

    async def _scrape_all(self, urls: list[str]) -> dict[str, str]:
        """並行 scrape，回傳 {url: 內文}；抓不到的是空字串。"""
        if not urls:
            return {}

        async def scrape_one(url: str) -> tuple[str, str]:
            with stage_timer(logger, "rag_web_scrape", url=url) as t_scrape:
                try:
                    scraped = (await self.web_client.scrape(url) or "").strip()
                except Exception:
                    scraped = ""
                # 逾時被 FirecrawlClient 吞掉後回空字串，從外面看不出
                # 「等滿逾時」與「頁面本來就沒內容」的差別——chars=0 配上
                # 一個接近逾時值的 ms，就是前者。
                t_scrape["chars"] = len(scraped)
            return url, scraped

        pairs = await asyncio.gather(*(scrape_one(url) for url in urls))
        return dict(pairs)

    @staticmethod
    def _is_cannot_answer(text: str) -> bool:
        return (
            matched_cannot_answer_marker(text, CANNOT_ANSWER_MARKERS) != "<none>"
        )

    @staticmethod
    def _append_sources(
        answer_text: str,
        docs: list[Document],
        dead_urls: frozenset[str] = frozenset(),
    ) -> str:
        """附上純文字來源清單，並把同一組來源交給呈現層做成按鈕。

        結構化來源與文字清單在同一個迴圈產生、共用同一個 display_idx，兩者
        因此不可能漂移——理由與 RagAnswerService._append_sources 相同。少了
        這一步，走網搜的回答在卡片路徑上會完全沒有來源：卡片內文的來源清單
        被 `strip_sources_section` 移除，而按鈕又無從產生。
        """
        source_lines: list[str] = []
        source_refs: list[SourceRef] = []
        seen_urls: set[str] = set()

        for doc in docs:
            if len(source_lines) >= CITE_TOP_K:
                break
            source_name = str(doc.metadata.get("source_name") or "").strip()
            url = str(doc.metadata.get("url") or "").strip()
            if not url or url in seen_urls or url in dead_urls:
                # 判死的整筆不顯示，與這條路徑既有的「沒有 url 就跳過」
                # 一致，而不是像知識庫路徑那樣退回「只顯示來源名」：網搜
                # 來源的 source_name 是搜尋結果標題（hit.title 空時甚至
                # 就是 url 本身），不是機構名，拿掉連結後剩下的字串對
                # 使用者驗證沒有價值。知識庫路徑的來源名是「食藥署」這種
                # 機構層級的名稱，才值得單獨保留。
                continue
            seen_urls.add(url)
            display_idx = len(source_lines) + 1
            label = source_name if source_name else url
            web_label = t("rag.web_source_label")
            source_lines.append(f"[{display_idx}] {web_label}：{label}：{url}")
            source_refs.append(
                SourceRef(index=display_idx, label=f"{web_label}：{label}", url=url)
            )

        if not source_lines:
            set_request_rag_sources(())
            return answer_text

        set_request_rag_sources(source_refs)
        heading = t("agent.sources_heading")
        return f"{answer_text}\n\n{heading}\n" + "\n".join(source_lines)
