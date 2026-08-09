"""User-scoped vector retriever for uploaded documents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langchain_core.documents import Document
from motor.motor_asyncio import AsyncIOMotorClient

from app.services.rag.retriever import _NUM_CANDIDATES_MULTIPLIER

DEFAULT_USER_DOCS_TOP_K = 5

# 這條路徑沒有 reranker——檢索結果直接進 prompt（見 UserDocumentAnswerService）。
# 因此不能沿用 KB 路徑「過濾交給 reranker」的放寬，必須自己保留品質門檻。
DEFAULT_USER_DOC_MIN_SCORE = 0.5


class UserDocumentVectorRetriever:
    """依 line_user_id 與未過期條件，在 user-docs collection 做向量檢索。"""

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
        k: int = DEFAULT_USER_DOCS_TOP_K,
        min_score: float = DEFAULT_USER_DOC_MIN_SCORE,
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
                ("MONGODB_USER_DOCS_COLLECTION", self.collection_name),
                ("MONGODB_USER_DOCS_VECTOR_INDEX", self.index_name),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing {', '.join(missing)}")

        client = AsyncIOMotorClient(self.mongo_uri)
        self._collection = client[self.db_name][self.collection_name]
        return self._collection

    async def ainvoke(self, query: str, *, line_user_id: str) -> list[Document]:
        if not (query or "").strip():
            return []
        if not (line_user_id or "").strip():
            return []

        query_embedding = await self.embeddings.aembed_query(query)
        if not query_embedding:
            raise ValueError("query_embedding cannot be empty")
        if self.vector_dim is not None and len(query_embedding) != self.vector_dim:
            raise ValueError(
                f"queryVector 維度必須為 {self.vector_dim}（與向量索引一致），"
                f"目前為 {len(query_embedding)}。"
            )

        now = datetime.now(timezone.utc)
        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.index_name,
                    "path": self.vector_field,
                    "queryVector": query_embedding,
                    "numCandidates": self.k * _NUM_CANDIDATES_MULTIPLIER,
                    "limit": self.k,
                    "filter": {
                        "line_user_id": {"$eq": line_user_id},
                        "expires_at": {"$gt": now},
                    },
                }
            },
            {
                "$project": {
                    self.text_field: 1,
                    "_id": 1,
                    "source_name": 1,
                    "document_id": 1,
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
                        "document_id": doc.get("document_id"),
                    },
                )
            )
        return documents
