# MongoDB Atlas 向量搜尋：設定、pipeline、store、結果型別。
# 頂層 retriever / embedding_gemini 為 RAG 對外入口；實作細節集中在此子套件。

from .config import VectorSearchConfig
from .store import MongoVectorSearchStore
from .types import ChunkHit, ChunkHits

__all__ = [
    "ChunkHit",
    "ChunkHits",
    "MongoVectorSearchStore",
    "VectorSearchConfig",
]
