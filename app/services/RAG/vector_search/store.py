from __future__ import annotations

from typing import Any, List, Optional

from pymongo import MongoClient
from pymongo.collection import Collection

from .config import VectorSearchConfig
from .mapping import mongo_document_to_chunk_hit
from .pipeline import build_vector_search_pipeline
from .types import ChunkHits
from .validation import (
    validate_config_ready,
    validate_query_embedding_dimension,
)


# 向量 chunk 查詢：負責協調「驗證 → pipeline → 執行 → 映射」。
# 連線延遲建立：__init__ 只保存 config，第一次查詢時才建立 MongoClient。
class MongoVectorSearchStore:

    def __init__(self, config: VectorSearchConfig) -> None:
        self._config = config
        self._client: MongoClient | None = None
        self._collection: Collection[Any] | None = None

    def _ensure_collection(self) -> Collection[Any]:
        # 延遲連線：避免 import / 測試時就連 Atlas；也讓 __init__ 保持輕量
        validate_config_ready(self._config)
        if self._collection is None:
            self._client = MongoClient(self._config.mongo_uri)
            self._collection = self._client[self._config.db_name][
                self._config.collection_name
            ]
        return self._collection

    def search_by_embedding(
        self,
        *,
        query_embedding: List[float],
        k: int = 10,
        num_candidates: Optional[int] = None,
    ) -> ChunkHits:
        validate_query_embedding_dimension(
            query_embedding, self._config.vector_dim
        )

        nc = self._config.resolve_num_candidates(k, per_call=num_candidates)

        pipeline = build_vector_search_pipeline(
            self._config,
            query_embedding=query_embedding,
            k=k,
            num_candidates=nc,
        )

        collection = self._ensure_collection()
        docs = list(collection.aggregate(pipeline))

        return [
            mongo_document_to_chunk_hit(
                doc,
                text_field=self._config.text_field,
            )
            for doc in docs
        ]
