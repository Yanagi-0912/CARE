"""MongoDB Atlas 向量檢索（async Runnable 風格：ainvoke）。"""

from __future__ import annotations

from typing import Any

from langchain_core.documents import Document
from motor.motor_asyncio import AsyncIOMotorClient

_NUM_CANDIDATES_MULTIPLIER = 30
DEFAULT_MIN_SCORE = 0.9


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
