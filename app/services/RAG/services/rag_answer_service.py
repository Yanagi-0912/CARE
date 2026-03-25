import logging
from typing import Protocol

from app.services.gemini import GeminiService
from app.services.RAG.client import embed_query
from app.services.RAG.retrieval.errors import RagNoHitsError
from app.services.RAG.retrieval.retriever import search_similar_chunks
from app.services.RAG.shared.vector_search import ChunkHits, MongoVectorSearchReader

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
            "請根據以下檢索到的醫療知識內容回答使用者問題。"
            "若內容不足，請明確說明不知道，勿捏造。\n\n"
            f"使用者問題：{user_text}\n\n"
            "檢索內容：\n"
            f"{chr(10).join(context_lines)}"
        )
        rag_result = await self.gemini_service.generate_response(rag_prompt)
        return rag_result.text or "抱歉，我目前找不到相關資料，請稍後再試。"
