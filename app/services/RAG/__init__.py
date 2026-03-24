# RAG：client（embedding）、retrieval（檢索流程）、shared（共用型別與 reader）。

from .client import embed_document, embed_query
from .retrieval import search_similar_chunks
from .shared.vector_search import ChunkHit, ChunkHits

__all__ = [
    "ChunkHit",
    "ChunkHits",
    "embed_document",
    "embed_query",
    "search_similar_chunks",
]
