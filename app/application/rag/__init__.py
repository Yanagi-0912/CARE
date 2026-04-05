# RAG：client（embedding_client）、services（RagAnswerService）、retrieval（retriever）、shared（reader）。

from .client import embed_document, embed_query
from .retrieval import search_similar_chunks
from app.infrastructure.vector_search import ChunkHit, ChunkHits

__all__ = [
    "ChunkHit",
    "ChunkHits",
    "embed_document",
    "embed_query",
    "search_similar_chunks",
]
