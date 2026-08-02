from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate

from app.services.gemini import GeminiService
from app.services.rag.web_client import WebSearchClient
from app.services.rag.whitelist import is_allowed_url, with_whitelist_site_filter

CITE_TOP_K = 3
NO_ANSWER_MESSAGE = "目前無法提供相關資訊，請稍後再試或換一種方式描述問題。"
CANNOT_ANSWER_MARKERS: tuple[str, ...] = (
    "不知道",
    "無法",
    "未找到",
    "找不到相關",
)
WEB_ANSWER_PREFIX = "以下參考網路公開資料"
WEB_SEARCH_LIMIT = 8
WEB_PAGE_CHAR_LIMIT = 8000

WEB_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "請根據以下提供的醫療知識內容回答問題。\n\n"
            "規則：\n"
            "1. 請在回答中適當引用內容來源的編號，例如：『...這是常見的症狀 [1]。』\n"
            "2. 回覆中不要使用「根據檢索內容」這類字眼，改用「根據公開網路資料」等說法。\n"
            "3. 請使用一般純文字，不要使用 Markdown 格式符號。\n"
            "4. 若內容不足，請明確說明不知道，勿捏造。\n\n"
            "使用者問題：{question}\n\n"
            "網路內容：\n"
            "{context}",
        )
    ]
)


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
            return NO_ANSWER_MESSAGE

        web_answer = await self._generate_answer(query, web_docs)
        if self._is_cannot_answer(web_answer):
            return NO_ANSWER_MESSAGE

        annotated = f"{WEB_ANSWER_PREFIX}\n\n{web_answer}"
        return self._append_sources(annotated, web_docs)

    async def _generate_answer(self, question: str, docs: list[Document]) -> str:
        context = "\n".join(
            f"{idx}. {doc.page_content}" for idx, doc in enumerate(docs, start=1)
        )
        messages = WEB_PROMPT.format_messages(question=question, context=context)
        result = await self.gemini_service.chat_model.ainvoke(messages)
        answer_text = result.content or "抱歉，我目前找不到相關資料，請稍後再試。"
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
            try:
                text = await self.web_client.scrape(url)
            except Exception:
                continue
            text = (text or "").strip()
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
        normalized = (text or "").strip()
        if not normalized:
            return True
        return any(marker in normalized for marker in CANNOT_ANSWER_MARKERS)

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
            source_lines.append(f"[{display_idx}] 網路：{label}：{url}")

        if not source_lines:
            return answer_text
        return f"{answer_text}\n\n參考資料來源：\n" + "\n".join(source_lines)
