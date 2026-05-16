import logging
from typing import Protocol

from app.services.gemini import GeminiService
from app.services.rag.client import embed_query
from app.services.rag.retrieval.errors import RagNoHitsError
from app.services.rag.retrieval.retriever import search_similar_chunks
from app.services.vector_search import ChunkHits, MongoVectorSearchReader

logger = logging.getLogger(__name__)


class EmbedQueryProvider(Protocol):
    async def embed_query(self, text: str) -> list[float]: ...


class SimilarChunkSearcher(Protocol):
    async def search_similar_chunks(
        self, query_embedding: list[float], reader: MongoVectorSearchReader
    ) -> ChunkHits: ...


class GeminiEmbedQueryProvider:
    async def embed_query(self, text: str) -> list[float]:
        return await embed_query(text)


class VectorSearchChunkSearcher:
    async def search_similar_chunks(
        self, query_embedding: list[float], reader: MongoVectorSearchReader
    ) -> ChunkHits:
        return await search_similar_chunks(query_embedding, reader)


class RagAnswerService:
    def __init__(
        self,
        gemini_service: GeminiService,
        vector_search_reader: MongoVectorSearchReader,
        embed_query_provider: EmbedQueryProvider | None = None,
        similar_chunk_searcher: SimilarChunkSearcher | None = None,
    ) -> None:
        self.gemini_service = gemini_service
        self.vector_search_reader = vector_search_reader
        self.embed_query_provider = (
            embed_query_provider or GeminiEmbedQueryProvider()
        )
        self.similar_chunk_searcher = (
            similar_chunk_searcher or VectorSearchChunkSearcher()
        )

    async def answer(self, user_text: str) -> str:
        query_vec = await self.embed_query_provider.embed_query(user_text)
        hits = await self.similar_chunk_searcher.search_similar_chunks(
            query_vec,
            self.vector_search_reader,
        )

        context_lines = []
        for idx, hit in enumerate(hits[:5], start=1):
            text = (hit.get("text") or "").strip()
            if text:
                context_lines.append(f"{idx}. {text}")

        if not context_lines:
            raise RagNoHitsError(user_text)

        rag_prompt = (
            "請根據以下提供的醫療知識內容回答問題。\n\n"
            "規則：\n"
            "1. 請在回答中適當引用內容來源的編號，例如：『...這是常見的症狀 [1]。』\n"
            "2. 回覆中不要使用「根據檢索內容」這類字眼，改用「根據 RAG 資訊」等說法。\n"
            "3. 請使用一般純文字，不要使用 Markdown 格式符號。\n"
            "4. 若內容不足，請明確說明不知道，勿捏造。\n\n"
            f"使用者問題：{user_text}\n\n"
            "RAG 內容：\n"
            f"{chr(10).join(context_lines)}"
        )
        from langchain_core.messages import HumanMessage
        rag_result = await self.gemini_service._chat_llm.ainvoke([HumanMessage(content=rag_prompt)])
        answer_text = rag_result.content or "抱歉，我目前找不到相關資料，請稍後再試。"
        if not isinstance(answer_text, str):
            answer_text = str(answer_text)
        return self._append_sources(answer_text, hits)

    @staticmethod
    def _append_sources(answer_text: str, hits: ChunkHits) -> str:
        source_lines: list[str] = []
        seen_urls: set[str] = set()

        # 這裡的 hits 順序應與 prompt 中的編號 1, 2, 3... 一致
        for idx, hit in enumerate(hits[:5], start=1):
            source_name = (hit.get("source_name") or "").strip()
            url = (hit.get("url") or "").strip()
            
            if not source_name or not url:
                continue
                
            # 避免重複列出相同的 URL
            if url in seen_urls:
                continue
            seen_urls.add(url)
            
            source_lines.append(f"[{idx}] {source_name}：{url}")

        if not source_lines:
            return answer_text

        return f"{answer_text}\n\n參考資料來源：\n" + "\n".join(source_lines)
