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


#驗證 → pipeline → aggregate → 映射
# 連線延遲建立：__init__ 只保存 DI 注入的 cfg，第一次查詢時才建立 AsyncIOMotorClient。
class MongoVectorSearchReader: #串流程用的
    # DI：VectorSearchConfig 由呼叫端注入，Reader 內不呼叫 from_settings()。
    def __init__(self, cfg: VectorSearchConfig) -> None:
        self._cfg = cfg
        self._client: AsyncIOMotorClient | None = None
        self._collection: AsyncIOMotorCollection | None = None

    def _ensure_collection(self) -> AsyncIOMotorCollection:
        # 延遲建立 client：避免 import / 測試時就連 Atlas；也讓 __init__ 保持輕量
        validate_config_ready(self._cfg)
        if self._collection is None:
            self._client = AsyncIOMotorClient(self._cfg.mongo_uri)
            self._collection = self._client[self._cfg.db_name][
                self._cfg.collection_name
            ]
        return self._collection

    # 前面的 * 表示：後面參數一定要用「關鍵字」呼叫。
    # 未傳 k 時：top_k 從注入的 cfg.default_top_k 讀取，非硬編碼。
    async def search_by_embedding(
        self,
        *,
        query_embedding: List[float],
        k: int | None = None,
    ) -> ChunkHits:
        cfg = self._cfg
        # 有傳 k 用呼叫端；否則從注入的 cfg 讀 default_top_k
        limit = cfg.default_top_k if k is None else k

        # 步驟 1：query 向量非空；再檢查長度是否與索引一致（有設 vector_dim 才檢查）
        validate_query_embedding_non_empty(query_embedding)
        validate_query_embedding_dimension(query_embedding, cfg.vector_dim)

        # 步驟 2：依 cfg 算出 $vectorSearch 的 numCandidates（要改行為請改 config）
        num_for_search = cfg.resolve_num_candidates(limit)

        # 步驟 3：組 Mongo 聚合管線（字串上仍是「查詢計畫」，還沒真的打資料庫）
        pipeline = build_vector_search_pipeline(
            cfg,
            query_embedding=query_embedding,
            k=limit,
            num_candidates=num_for_search,
        )

        # 步驟 4：取得 Mongo collection（第一次會建立 Motor client）
        collection = self._ensure_collection()

        # 步驟 5：Motor 非同步 aggregate（不阻塞 asyncio event loop）
        cursor = collection.aggregate(pipeline)
        raw_docs: list[dict[str, Any]] = await cursor.to_list(length=None)

        # 步驟 6：每筆文件轉成對外契約 ChunkHit，收集成列表
        hits: list[ChunkHit] = []
        text_field = cfg.text_field
        for doc in raw_docs:
            hit = mongo_document_to_chunk_hit(doc, text_field=text_field)
            hits.append(hit)

        return hits
