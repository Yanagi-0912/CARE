from typing import Any, Dict, List

from .mongo_vector_store_example import MongoVectorStoreExample


def retrieve_top_k_by_vector(
    query_vector: List[float],
    *,
    k: int = 10,
) -> List[Dict[str, Any]]:
    if not query_vector:
        raise ValueError("query_vector cannot be empty")

    store = MongoVectorStoreExample()
    return store.vector_search(query_vector=query_vector, k=k)
