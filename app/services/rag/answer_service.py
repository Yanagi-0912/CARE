import logging

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate

from app.services.gemini import GeminiService
from app.i18n.messages import t
from app.services.rag.cohere_reranker import Reranker, VectorScoreReranker
from app.services.rag.fail_messages import (
    NO_ANSWER_MESSAGE,
    NO_HITS_MESSAGE,
    RagFailCode,
    rag_fail,
)
from app.services.rag.query_rewriter import QueryRewriter
from app.services.rag.retrieval_grader import Grade, RetrievalGrader
from app.services.rag.retriever import MongoAtlasVectorRetriever
from app.services.rag.web_search_service import WebSearchService

logger = logging.getLogger(__name__)

# Wide retrieve candidates（依賴注入可用 settings 覆寫 retriever.k）
RETRIEVAL_TOP_K = 40
RERANK_TOP_N = 5
CITE_TOP_K = 3
CANNOT_ANSWER_MARKERS: tuple[str, ...] = (
    "不知道",
    "無法",
    "未找到",
    "找不到相關",
)

RAG_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "請根據以下提供的醫療知識內容回答問題。\n\n"
            "規則：\n"
            "1. 請在回答中適當引用內容來源的編號，例如：『...這是常見的症狀 [1]。』\n"
            "2. 回覆中不要使用「根據檢索內容」這類字眼，改用「根據 RAG 資訊」等說法。\n"
            "3. 請使用一般純文字，不要使用 Markdown 格式符號。\n"
            "4. 若內容不足，請明確說明不知道，勿捏造。\n\n"
            "使用者問題：{question}\n\n"
            "RAG 內容：\n"
            "{context}",
        )
    ]
)


class RagAnswerService:
    def __init__(
        self,
        gemini_service: GeminiService,
        retriever: MongoAtlasVectorRetriever,
        reranker: Reranker | None = None,
        *,
        rerank_top_n: int = RERANK_TOP_N,
        grader: RetrievalGrader | None = None,
        rewriter: QueryRewriter | None = None,
        crag_enabled: bool = False,
        web_search: WebSearchService | None = None,
        web_fallback_enabled: bool = False,
    ) -> None:
        self.gemini_service = gemini_service
        self.retriever = retriever
        self.reranker: Reranker = reranker or VectorScoreReranker()
        self.rerank_top_n = rerank_top_n
        self.grader = grader
        self.rewriter = rewriter
        self.crag_enabled = crag_enabled and grader is not None
        self.web_search = web_search
        self.web_fallback_enabled = bool(web_fallback_enabled and web_search is not None)

    async def answer(self, user_text: str) -> str:
        ranked = await self._retrieve_and_rerank(user_text)
        if not ranked:
            return await self._web_or_no_hits(user_text)

        if self.crag_enabled:
            try:
                ranked = await self._apply_crag(user_text, ranked)
            except Exception:
                logger.exception(
                    "CRAG failed; degrading to generate without grade crag_grade=degraded"
                )
            else:
                if ranked is None:
                    return await self._web_or_no_hits(user_text)

        kb_answer = await self._generate_answer(user_text, ranked)
        if self._is_cannot_answer(kb_answer):
            return self._fail(RagFailCode.MODEL_REFUSE)

        return self._append_sources(kb_answer, ranked)

    async def _web_or_no_hits(self, query: str) -> str:
        if not self.web_fallback_enabled or self.web_search is None:
            return self._fail(RagFailCode.KB_EMPTY)
        try:
            return await self.web_search.answer(query)
        except Exception:
            logger.exception("web fallback failed")
            return self._fail(RagFailCode.WEB_ERROR)

    @staticmethod
    def _fail(code: str) -> str:
        logger.info("rag_fail code=%s", code)
        return rag_fail(code)

    async def _retrieve_and_rerank(self, query: str) -> list[Document]:
        docs = await self.retriever.ainvoke(query)
        if not docs:
            return []
        return await self.reranker.rerank(query, docs, top_n=self.rerank_top_n)

    async def _apply_crag(
        self, user_text: str, ranked: list[Document]
    ) -> list[Document] | None:
        """回傳可用於生成的 docs；None 表示知識庫不足。"""
        assert self.grader is not None
        grade = await self.grader.grade(user_text, ranked)
        logger.info("crag_grade=%s", grade.value)

        if grade is Grade.CORRECT:
            return ranked

        if grade is Grade.INCORRECT:
            return None

        # ambiguous
        if self.rewriter is None:
            logger.info("crag_grade=ambiguous_no_rewriter")
            return None

        try:
            rewritten = await self.rewriter.rewrite(user_text, ranked)
        except Exception:
            logger.exception(
                "CRAG rewrite failed; degrading to generate crag_grade=rewrite_degraded"
            )
            return ranked

        second = await self._retrieve_and_rerank(rewritten)
        if not second:
            logger.info("crag_grade=ambiguous_exhausted empty_retry")
            return None

        grade2 = await self.grader.grade(rewritten, second)
        logger.info("crag_grade=%s after_rewrite", grade2.value)
        if grade2 is Grade.CORRECT:
            return second
        return None

    async def _generate_answer(self, question: str, docs: list[Document]) -> str:
        context = "\n".join(
            f"{idx}. {doc.page_content}" for idx, doc in enumerate(docs, start=1)
        )
        messages = RAG_PROMPT.format_messages(question=question, context=context)
        rag_result = await self.gemini_service.chat_model.ainvoke(messages)
        answer_text = rag_result.content or "抱歉，我目前找不到相關資料，請稍後再試。"
        if not isinstance(answer_text, str):
            answer_text = str(answer_text)
        return answer_text

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
            if source_name:
                source_lines.append(f"[{display_idx}] {source_name}：{url}")
            else:
                source_lines.append(f"[{display_idx}] {url}")

        if not source_lines:
            return answer_text
        heading = t("agent.sources_heading")
        return f"{answer_text}\n\n{heading}\n" + "\n".join(source_lines)
