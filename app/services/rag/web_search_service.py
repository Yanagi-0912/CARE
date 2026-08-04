import logging

from langchain_core.documents import Document

from app.services.gemini import GeminiService
from app.i18n.messages import t
from app.services.rag.cannot_answer import (
    CANNOT_ANSWER_MARKERS,
    answer_preview,
    matched_cannot_answer_marker,
)
from app.services.rag.answer_prompts import build_web_prompt
from app.services.rag.fail_messages import (
    NO_ANSWER_MESSAGE,
    RagFailCode,
    rag_fail,
)
from app.services.rag.web_client import WebSearchClient
from app.services.rag.whitelist import is_allowed_url, with_whitelist_site_filter

logger = logging.getLogger(__name__)

CITE_TOP_K = 3
# 相容舊測試／匯入：預設繁中文案
WEB_ANSWER_PREFIX = "以下參考網路公開資料"
WEB_SEARCH_LIMIT = 8
WEB_PAGE_CHAR_LIMIT = 8000
# search snippet 達此長度就不打 scrape（避免 gov.tw 頁面常逾時）
WEB_SNIPPET_MIN_CHARS = 20


def web_answer_prefix(language: str | None = None) -> str:
    return t("rag.web_answer_prefix", language=language)


class WebSearchService:
    def __init__(
        self,
        gemini_service: GeminiService,
        web_client: WebSearchClient | None = None,
    ) -> None:
        self.gemini_service = gemini_service
        self.web_client = web_client

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
        return self._append_sources(annotated, web_docs)

    async def _generate_answer(self, question: str, docs: list[Document]) -> str:
        context = "\n".join(
            f"{idx}. {doc.page_content}" for idx, doc in enumerate(docs, start=1)
        )
        messages = build_web_prompt().format_messages(
            question=question, context=context
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
            url = (hit.url or "").strip()
            if not url or url in seen or not is_allowed_url(url):
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
        source_lines: list[str] = []
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

        if not source_lines:
            return answer_text
        heading = t("agent.sources_heading")
        return f"{answer_text}\n\n{heading}\n" + "\n".join(source_lines)
