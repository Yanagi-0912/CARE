from __future__ import annotations

from typing import Any, List

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from .config import VectorSearchConfig
from .mapping import mongo_document_to_chunk_hit
from .pipeline import build_vector_search_pipeline
from .types import ChunkHit, ChunkHits
from .validation import (
    validate_config_ready,
    validate_query_embedding_dimension,
    validate_query_embedding_non_empty,
)


class MongoVectorSearchReader:
    def __init__(self, cfg: VectorSearchConfig) -> None:
        self._cfg = cfg
        self._client: AsyncIOMotorClient | None = None
        self._collection: AsyncIOMotorCollection | None = None

    def _ensure_collection(self) -> AsyncIOMotorCollection:
        validate_config_ready(self._cfg)
        if self._collection is None:
            self._client = AsyncIOMotorClient(self._cfg.mongo_uri)
            self._collection = self._client[self._cfg.db_name][
                self._cfg.collection_name
            ]
        return self._collection

    async def search_by_embedding(
        self,
        *,
        query_embedding: List[float],
        k: int | None = None,
    ) -> ChunkHits:
        cfg = self._cfg
        limit = cfg.default_top_k if k is None else k
        validate_query_embedding_non_empty(query_embedding)
        validate_query_embedding_dimension(query_embedding, cfg.vector_dim)

        num_for_search = cfg.resolve_num_candidates(limit)
        pipeline = build_vector_search_pipeline(
            cfg,
            query_embedding=query_embedding,
            k=limit,
            num_candidates=num_for_search,
        )

        collection = self._ensure_collection()
        cursor = collection.aggregate(pipeline)
        raw_docs: list[dict[str, Any]] = await cursor.to_list(length=None)

        hits: list[ChunkHit] = []
        text_field = cfg.text_field
        for doc in raw_docs:
            hit = mongo_document_to_chunk_hit(doc, text_field=text_field)
            hits.append(hit)
        return hits
