from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.services.rag.chunking import split_text_to_chunks


class UserDocumentIngestService:
    def __init__(
        self,
        *,
        embeddings: Any,
        collection: Any,
        text_field: str = "text",
        vector_field: str = "embedding",
        vector_dim: int | None = None,
        ttl_seconds: int = 86400,
    ) -> None:
        self.embeddings = embeddings
        self.collection = collection
        self.text_field = text_field
        self.vector_field = vector_field
        self.vector_dim = vector_dim
        self.ttl_seconds = ttl_seconds

    async def ingest_text(
        self,
        line_user_id: str,
        text: str,
        *,
        source_name: str = "",
        media_type: str = "file",
    ) -> str:
        if not line_user_id or not line_user_id.strip():
            return ""
        if not text or not text.strip():
            return ""

        chunks = split_text_to_chunks(text)
        if not chunks:
            return ""

        vectors = await self.embeddings.aembed_documents(chunks)
        if len(vectors) != len(chunks):
            raise ValueError("Embedding count mismatch")

        if self.vector_dim is not None:
            for i, vector in enumerate(vectors):
                if len(vector) != self.vector_dim:
                    raise ValueError(f"Embedding dimension mismatch at chunk {i}")

        document_id = str(uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self.ttl_seconds)
        ingested_at = now.isoformat()

        docs = [
            {
                self.text_field: chunk,
                self.vector_field: vector,
                "line_user_id": line_user_id,
                "document_id": document_id,
                "source_name": source_name,
                "media_type": media_type,
                "chunk_index": index,
                "content_hash": hashlib.sha256(chunk.encode()).hexdigest(),
                "ingested_at": ingested_at,
                "expires_at": expires_at,
            }
            for index, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]

        await self.collection.insert_many(docs)
        return document_id
