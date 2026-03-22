from typing import List, Optional

from .vector_search import ChunkHits, MongoVectorSearchStore, VectorSearchConfig

_config: Optional[VectorSearchConfig] = None
_store: Optional[MongoVectorSearchStore] = None


def _get_vector_search_config() -> VectorSearchConfig:
    global _config
    if _config is None:
        _config = VectorSearchConfig.from_settings()
    return _config


def _get_vector_store() -> MongoVectorSearchStore:
    global _store
    if _store is None:
        _store = MongoVectorSearchStore(_get_vector_search_config())
    return _store


def search_similar_chunks(
    query_embedding: List[float],
    *,
    k: Optional[int] = None,
) -> ChunkHits:
    # 依使用者問題的 embedding，從向量索引取回語意最相近的 k 筆 chunk。
    if not query_embedding:
        raise ValueError("query_embedding cannot be empty")

    cfg = _get_vector_search_config()
    top_k = k if k is not None else cfg.default_top_k

    return _get_vector_store().search_by_embedding(
        query_embedding=query_embedding,
        k=top_k,
    )
