"""MongoDB Atlas 檢索（async Runnable 風格：ainvoke）。

三個實作共用同一個 `ainvoke(query) -> list[Document]` 介面，因此可以互換
注入 `RagAnswerService`，不需改動下游任何流程：

- `MongoAtlasVectorRetriever`：`$vectorSearch`，比對語意。
- `MongoAtlasTextRetriever`：`$search`，比對字面（BM25）。醫療查詢裡的
  藥名、劑量、疾病名這類罕見精確詞是稠密向量的弱項、稀疏檢索的強項。
- `HybridRetriever`：並行跑上面兩者，再以 RRF 融合。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.documents import Document
from motor.motor_asyncio import AsyncIOMotorClient

from app.services.rag.rank_fusion import DEFAULT_RRF_K, reciprocal_rank_fusion

logger = logging.getLogger(__name__)

_NUM_CANDIDATES_MULTIPLIER = 30
DEFAULT_MIN_SCORE = 0.5

VECTOR_SOURCE_NAME = "vector"
TEXT_SOURCE_NAME = "text"


class MongoAtlasVectorRetriever:
    """問題字串 → embedding → MongoDB `$vectorSearch` → `Document` 列表。"""

    def __init__(
        self,
        *,
        embeddings: Any,
        mongo_uri: str,
        db_name: str,
        collection_name: str,
        index_name: str,
        vector_field: str = "embedding",
        text_field: str = "text",
        vector_dim: int | None = None,
        k: int = 10,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> None:
        self.embeddings = embeddings
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.collection_name = collection_name
        self.index_name = index_name
        self.vector_field = vector_field
        self.text_field = text_field
        self.vector_dim = vector_dim
        self.k = k
        self.min_score = min_score
        self._collection: Any = None

    def _ensure_collection(self) -> Any:
        if self._collection is not None:
            return self._collection

        missing = [
            name
            for name, value in (
                ("MONGODB_URI", self.mongo_uri),
                ("MONGODB_DB", self.db_name),
                ("MONGODB_COLLECTION", self.collection_name),
                ("MONGODB_VECTOR_INDEX", self.index_name),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing {', '.join(missing)}")

        client = AsyncIOMotorClient(self.mongo_uri)
        self._collection = client[self.db_name][self.collection_name]
        return self._collection

    async def ainvoke(self, query: str) -> list[Document]:
        query_embedding = await self.embeddings.aembed_query(query)
        if not query_embedding:
            raise ValueError("query_embedding cannot be empty")
        if self.vector_dim is not None and len(query_embedding) != self.vector_dim:
            raise ValueError(
                f"queryVector 維度必須為 {self.vector_dim}（與向量索引一致），"
                f"目前為 {len(query_embedding)}。"
                " 請用與建索引時相同的 embedding 模型產生完整向量。"
            )

        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.index_name,
                    "path": self.vector_field,
                    "queryVector": query_embedding,
                    "numCandidates": self.k * _NUM_CANDIDATES_MULTIPLIER,
                    "limit": self.k,
                }
            },
            {
                "$project": {
                    self.text_field: 1,
                    "_id": 1,
                    "source_name": 1,
                    "url": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]

        raw_docs = await self._ensure_collection().aggregate(pipeline).to_list(length=None)

        documents: list[Document] = []
        for doc in raw_docs:
            text = str(doc.get(self.text_field) or "").strip()
            if not text:
                continue
            score = doc.get("score")
            if not isinstance(score, (int, float)) or score < self.min_score:
                continue
            documents.append(
                Document(
                    page_content=text,
                    metadata={
                        "id": str(doc.get("_id")),
                        "score": score,
                        "source_name": doc.get("source_name"),
                        "url": doc.get("url"),
                    },
                )
            )
        return documents


class MongoAtlasTextRetriever:
    """問題字串 → MongoDB Atlas `$search`（BM25）→ `Document` 列表。

    索引必須是 Atlas Search index（與 vector index 是兩個不同的東西），
    且中文語料的 analyzer 必須用 `lucene.cjk`；預設的英文 analyzer 會把
    整句中文當成一個 token，BM25 等於失效。
    """

    def __init__(
        self,
        *,
        mongo_uri: str,
        db_name: str,
        collection_name: str,
        index_name: str,
        text_field: str = "text",
        k: int = 10,
    ) -> None:
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.collection_name = collection_name
        self.index_name = index_name
        self.text_field = text_field
        self.k = k
        self._collection: Any = None

    def _ensure_collection(self) -> Any:
        if self._collection is not None:
            return self._collection

        missing = [
            name
            for name, value in (
                ("MONGODB_URI", self.mongo_uri),
                ("MONGODB_DB", self.db_name),
                ("MONGODB_COLLECTION", self.collection_name),
                ("MONGODB_TEXT_INDEX", self.index_name),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing {', '.join(missing)}")

        client = AsyncIOMotorClient(self.mongo_uri)
        self._collection = client[self.db_name][self.collection_name]
        return self._collection

    async def ainvoke(self, query: str) -> list[Document]:
        if not (query or "").strip():
            return []

        pipeline = [
            {
                "$search": {
                    "index": self.index_name,
                    "text": {"query": query, "path": self.text_field},
                }
            },
            {"$limit": self.k},
            {
                "$project": {
                    self.text_field: 1,
                    "_id": 1,
                    "source_name": 1,
                    "url": 1,
                    "score": {"$meta": "searchScore"},
                }
            },
        ]

        raw_docs = await self._ensure_collection().aggregate(pipeline).to_list(length=None)

        documents: list[Document] = []
        for doc in raw_docs:
            text = str(doc.get(self.text_field) or "").strip()
            if not text:
                continue
            # 刻意不做 min_score 過濾：BM25 分數沒有上界、也依語料庫統計而變，
            # 固定門檻沒有意義（向量的 0.5 門檻是針對 cosine 相似度才成立）。
            score = doc.get("score")
            documents.append(
                Document(
                    page_content=text,
                    metadata={
                        "id": str(doc.get("_id")),
                        "score": float(score) if isinstance(score, (int, float)) else 0.0,
                        "source_name": doc.get("source_name"),
                        "url": doc.get("url"),
                    },
                )
            )
        return documents


class HybridRetriever:
    """並行跑向量與文字檢索，再以 RRF 融合成單一排名。

    任一邊失敗只記錄並降級為另一邊的結果（fail-open）。這讓本類別在
    Atlas Search index 還沒建好時也能安全上線 —— 那時 `$search` 會報錯，
    行為自動退化為原本的純向量檢索。
    """

    def __init__(
        self,
        *,
        vector_retriever: Any,
        text_retriever: Any,
        rrf_k: int = DEFAULT_RRF_K,
        limit: int | None = None,
    ) -> None:
        self.vector_retriever = vector_retriever
        self.text_retriever = text_retriever
        self.rrf_k = rrf_k
        self.limit = limit

    async def ainvoke(self, query: str) -> list[Document]:
        vector_docs, text_docs = await asyncio.gather(
            self._safe_invoke(self.vector_retriever, VECTOR_SOURCE_NAME, query),
            self._safe_invoke(self.text_retriever, TEXT_SOURCE_NAME, query),
        )

        fused = reciprocal_rank_fusion(
            [
                (VECTOR_SOURCE_NAME, vector_docs),
                (TEXT_SOURCE_NAME, text_docs),
            ],
            k=self.rrf_k,
            limit=self.limit,
        )
        logger.info(
            "hybrid_retrieve vector=%d text=%d fused=%d",
            len(vector_docs),
            len(text_docs),
            len(fused),
        )
        return fused

    @staticmethod
    async def _safe_invoke(retriever: Any, name: str, query: str) -> list[Document]:
        try:
            return await retriever.ainvoke(query)
        except Exception:
            logger.exception("hybrid_retrieve_failed source=%s; degrading", name)
            return []
