import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.documents import Document

from app.core.rag_sources import SourceRef, set_request_rag_sources
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
    ) -> None:
        self.gemini_service = gemini_service
        self.web_client = web_client
        self._on_web_fallback_success = on_web_fallback_success

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
        result = self._append_sources(annotated, web_docs)
        await self._maybe_create_knowledge_report(query, web_docs)
        return result

    async def _maybe_create_knowledge_report(
        self, query: str, web_docs: list[Document]
    ) -> None:
        if self._on_web_fallback_success is None:
            return

        urls = self._extract_source_urls(web_docs)
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
    def _extract_source_urls(docs: list[Document]) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for doc in docs:
            if len(urls) >= CITE_TOP_K:
                break
            url = str(doc.metadata.get("url") or "").strip()
            if not url or url in seen:
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
        result = await self.gemini_service.chat_model.ainvoke(messages)
        answer_text = result.content or t("rag.generate_fallback")
        if not isinstance(answer_text, str):
            answer_text = str(answer_text)
        return answer_text

    async def _fetch_web_docs(self, query: str) -> list[Document]:
        if self.web_client is None:
            return []
        try:
            hits = await self.web_client.search(
                with_whitelist_site_filter(query),
                limit=WEB_SEARCH_LIMIT,
            )
        except Exception:
            return []

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
                try:
                    scraped = (await self.web_client.scrape(url) or "").strip()
                except Exception:
                    scraped = ""
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
        return docs

    @staticmethod
    def _is_cannot_answer(text: str) -> bool:
        return (
            matched_cannot_answer_marker(text, CANNOT_ANSWER_MARKERS) != "<none>"
        )

    @staticmethod
    def _append_sources(answer_text: str, docs: list[Document]) -> str:
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
            if not url or url in seen_urls:
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
