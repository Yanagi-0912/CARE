from typing import List, Optional

from .config import VectorSearchConfig


def validate_config_ready(config: VectorSearchConfig) -> None:
    if not config.mongo_uri:
        raise ValueError("Missing MONGODB_URI")
    if not config.db_name:
        raise ValueError("Missing MONGODB_DB")
    if not config.collection_name:
        raise ValueError("Missing MONGODB_COLLECTION")
    if not config.vector_index:
        raise ValueError("Missing MONGODB_VECTOR_INDEX")


def validate_query_embedding_non_empty(query_embedding: List[float]) -> None:
    if not query_embedding:
        raise ValueError("query_embedding cannot be empty")


def validate_query_embedding_dimension(
    query_embedding: List[float],
    vector_dim: Optional[int],
) -> None:
    if vector_dim is None:
        return
    if len(query_embedding) != vector_dim:
        raise ValueError(
            f"queryVector 維度必須為 {vector_dim}（與向量索引一致），"
            f"目前為 {len(query_embedding)}。"
            " 請用與建索引時相同的 embedding 模型產生完整向量。"
        )
