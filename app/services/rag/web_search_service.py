import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.documents import Document

from app.core.rag_sources import SourceRef, set_request_rag_sources
from app.core.request_logging import stage_timer
from app.core.request_context import get_line_user_id
from app.services.gemini import GeminiService
from app.i18n.messages import t
from app.services.rag.cannot_answer import (
    CANNOT_ANSWER_MARKERS,
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
from app.services.rag.web_client import WebSearchClient
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


class WebSearchService:
    def __init__(
        self,
        gemini_service: GeminiService,
        web_client: WebSearchClient | None = None,
        on_web_fallback_success: OnWebFallbackSuccess | None = None,
        link_checker: LinkChecker | None = None,
    ) -> None:
        self.gemini_service = gemini_service
        self.web_client = web_client
        self._on_web_fallback_success = on_web_fallback_success
        # None＝不檢查來源網址存活，行為與導入前完全相同（見 link_check.py）
        self.link_checker = link_checker

    async def answer(self, query: str) -> str:
        web_docs = await self._fetch_web_docs(query)
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
        answer_text = result.content or t("rag.generate_fallback")
        if not isinstance(answer_text, str):
            answer_text = str(answer_text)
        return answer_text

    async def _fetch_web_docs(self, query: str) -> list[Document]:
        if self.web_client is None:
            return []
        with stage_timer(logger, "rag_web_search") as t_search:
            try:
                hits = await self.web_client.search(
                    with_whitelist_site_filter(query),
                    limit=WEB_SEARCH_LIMIT,
                )
            except Exception:
                t_search["hits"] = "error"
                return []
            t_search["hits"] = len(hits)

        # scrape 是逐一 await 的，整段的 ms 與次數要分開記：單次 scrape 不慢
        # 但跑了六次，與單次就卡滿逾時，是兩個不同的問題、兩種不同的修法。
        with stage_timer(logger, "rag_web_scrape_loop") as t_loop:
            docs = await self._collect_web_docs(hits, t_loop)
        return docs

    async def _collect_web_docs(
        self, hits: list[Any], t_loop: dict[str, Any]
    ) -> list[Document]:
        scrapes = 0
        docs: list[Document] = []
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
            # 優先用 search snippet，避免 Firecrawl scrape 15–45s 連逾時拖死整輪
            text = (hit.description or "").strip()
            if len(text) < WEB_SNIPPET_MIN_CHARS:
                scrapes += 1
                with stage_timer(logger, "rag_web_scrape", url=url) as t_scrape:
                    try:
                        scraped = (await self.web_client.scrape(url) or "").strip()
                    except Exception:
                        scraped = ""
                    # 逾時被 FirecrawlClient 吞掉後回空字串，從外面看不出
                    # 「等滿逾時」與「頁面本來就沒內容」的差別——chars=0 配上
                    # 一個接近逾時值的 ms，就是前者。
                    t_scrape["chars"] = len(scraped)
                if scraped:
                    text = scraped
            if not text:
                continue
            seen.add(url)
            docs.append(
                Document(
                    page_content=text[:WEB_PAGE_CHAR_LIMIT],
                    metadata={
                        "source_name": (hit.title or "").strip() or url,
                        "url": url,
                    },
                )
            )
            if len(docs) >= CITE_TOP_K:
                break
        t_loop["hits"] = len(hits)
        t_loop["scrapes"] = scrapes
        t_loop["docs"] = len(docs)
        return docs

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
