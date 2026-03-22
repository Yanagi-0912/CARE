# RAG：embedding（頂層）與向量檢索（vector_search 子套件）。

from .embedding_gemini import embed_document, embed_query
from .retriever import search_similar_chunks
from .vector_search import ChunkHit, ChunkHits

__all__ = [
    "ChunkHit",
    "ChunkHits",
    "embed_document",
    "embed_query",
    "search_similar_chunks",
]
