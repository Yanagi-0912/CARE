import os
from typing import Any, Dict, List, Optional


class MongoVectorStoreExample:
    def __init__(
        self,
        *,
        mongo_uri: Optional[str] = None,
        db_name: Optional[str] = None,
        collection_name: Optional[str] = None,
        vector_index: Optional[str] = None,
        vector_field: Optional[str] = None,
        text_field: Optional[str] = None,
    ) -> None:
        self.mongo_uri = mongo_uri or os.getenv("MONGODB_URI", "")
        self.db_name = db_name or os.getenv("MONGODB_DB", "")
        self.collection_name = collection_name or os.getenv("MONGODB_COLLECTION", "")

        self.vector_index = vector_index or os.getenv("MONGODB_VECTOR_INDEX", "")
        self.vector_field = vector_field or os.getenv("MONGODB_VECTOR_FIELD", "embedding")
        self.text_field = text_field or os.getenv("MONGODB_TEXT_FIELD", "text")
        # MONGODB_VECTOR_DIM 與 Atlas 索引維度一致
        _dim = os.getenv("MONGODB_VECTOR_DIM", "").strip()
        self.vector_dim: Optional[int] = int(_dim) if _dim else None

    def vector_search(
        self,
        *,
        query_vector: List[float],
        k: int = 10,
        num_candidates: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if not self.mongo_uri:
            raise ValueError("Missing MONGODB_URI")
        if not self.db_name:
            raise ValueError("Missing MONGODB_DB")
        if not self.collection_name:
            raise ValueError("Missing MONGODB_COLLECTION")
        if not self.vector_index:
            raise ValueError("Missing MONGODB_VECTOR_INDEX")

        expected = self.vector_dim
        if expected is not None and len(query_vector) != expected:
            raise ValueError(
                f"queryVector 維度必須為 {expected}（與向量索引一致），"
                f"目前為 {len(query_vector)}。"
                " 請用與建索引時相同的 embedding 模型產生「完整」向量，"
                "不要用 [0.1,0.2,0.3] 這種測試短向量。"
            )

        try:
            from pymongo import MongoClient
        except ImportError as e:
            raise ImportError(
                "pymongo is required for Mongo vector search. Install it first: pip install pymongo"
            ) from e

        client = MongoClient(self.mongo_uri)
        collection = client[self.db_name][self.collection_name]

        num_candidates = num_candidates or max(k * 10, 100)

        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.vector_index,
                    "path": self.vector_field,
                    "queryVector": query_vector,
                    "numCandidates": num_candidates,
                    "limit": k,
                }
            },
            {
                "$project": {
                    self.text_field: 1,
                    "_id": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]

        docs = list(collection.aggregate(pipeline))
        results: List[Dict[str, Any]] = []
        for d in docs:
            results.append(
                {
                    "id": str(d.get("_id")),
                    "text": d.get(self.text_field),
                    "score": d.get("score"),
                }
            )
        return results
