from typing import Any, List

from .config import VectorSearchConfig


def build_vector_search_pipeline(
    config: VectorSearchConfig,
    *,
    query_embedding: List[float],
    k: int,
    num_candidates: int,
) -> List[dict[str, Any]]:
    return [
        {
            "$vectorSearch": {
                "index": config.vector_index,
                "path": config.vector_field,
                "queryVector": query_embedding,
                "numCandidates": num_candidates,
                "limit": k,
            }
        },
        {
            "$project": {
                config.text_field: 1,
                "_id": 1,
                "source_name": 1,
                "url": 1,
                "score": {"$meta": "vectorSearchScore"},
            }
        },
    ]
