import logging
from typing import Protocol

from app.infrastructure.gemini import GeminiService
from app.application.rag.client import embed_query
from app.application.rag.retrieval.errors import RagNoHitsError
from app.application.rag.retrieval.retriever import search_similar_chunks
from app.infrastructure.vector_search import ChunkHits, MongoVectorSearchReader

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
            "請根據以下 RAG 提供的醫療知識內容回答使用者問題。"
            "回覆中不要使用「根據檢索內容」這類字眼，改用「根據 RAG 資訊」等說法。"
            "請使用一般純文字，不要使用 Markdown 格式符號。"
            "若內容不足，請明確說明不知道，勿捏造。\n\n"
            f"使用者問題：{user_text}\n\n"
            "RAG 內容：\n"
            f"{chr(10).join(context_lines)}"
        )
        rag_result = await self.gemini_service.generate_response(rag_prompt)
        answer_text = rag_result.text or "抱歉，我目前找不到相關資料，請稍後再試。"
        return self._append_sources(answer_text, hits)

    @staticmethod
    def _append_sources(answer_text: str, hits: ChunkHits) -> str:
        max_sources = 3
        source_lines: list[str] = []
        seen: set[tuple[str, str]] = set()

        sorted_hits = sorted(
            hits,
            key=lambda h: h.get("score") if h.get("score") is not None else float("-inf"),
            reverse=True,
        )

        for hit in sorted_hits:
            source_name = (hit.get("source_name") or "").strip()
            url = (hit.get("url") or "").strip()
            if not source_name or not url:
                continue

            key = (source_name, url)
            if key in seen:
                continue
            seen.add(key)
            source_lines.append(f"- {source_name}：{url}")
            if len(source_lines) >= max_sources:
                break

        if not source_lines:
            return answer_text

        return f"{answer_text}\n\n資料來源：\n" + "\n".join(source_lines)
