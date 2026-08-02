from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from app.services.rag.chunking import split_text_to_chunks
from app.services.rag.whitelist import is_allowed_url

IngestStatus = Literal["ok", "rejected", "empty", "error"]


@dataclass(frozen=True)
class IngestResult:
    status: IngestStatus
    url: str
    chunk_count: int
    message: str = ""


class IngestService:
    def __init__(
        self,
        *,
        web_client: Any,
        embeddings: Any,
        collection: Any,
        text_field: str = "text",
        vector_field: str = "embedding",
        vector_dim: int | None = None,
    ) -> None:
        self.web_client = web_client
        self.embeddings = embeddings
        self.collection = collection
        self.text_field = text_field
        self.vector_field = vector_field
        self.vector_dim = vector_dim

    async def ingest_url(self, url: str, *, source_name: str | None = None) -> IngestResult:
        if not is_allowed_url(url):
            return IngestResult(
                status="rejected",
                url=url,
                chunk_count=0,
                message="URL not in whitelist",
            )

        try:
            scraped = await self.web_client.scrape(url)
        except Exception as exc:
            return IngestResult(
                status="error",
                url=url,
                chunk_count=0,
                message=str(exc),
            )

        if not scraped or not scraped.strip():
            return IngestResult(
                status="empty",
                url=url,
                chunk_count=0,
                message="Scrape returned empty content",
            )

        chunks = split_text_to_chunks(scraped)
        if not chunks:
            return IngestResult(
                status="empty",
                url=url,
                chunk_count=0,
                message="No chunks after splitting",
            )

        try:
            vectors = await self.embeddings.aembed_documents(chunks)
        except Exception as exc:
            return IngestResult(
                status="error",
                url=url,
                chunk_count=0,
                message=str(exc),
            )

        if len(vectors) != len(chunks):
            return IngestResult(
                status="error",
                url=url,
                chunk_count=0,
                message="Embedding count mismatch",
            )

        if self.vector_dim is not None:
            for i, vector in enumerate(vectors):
                if len(vector) != self.vector_dim:
                    return IngestResult(
                        status="error",
                        url=url,
                        chunk_count=0,
                        message=f"Embedding dimension mismatch at chunk {i}",
                    )

        ingested_at = datetime.now(timezone.utc).isoformat()
        resolved_source = source_name or ""

        docs = [
            {
                self.text_field: chunk,
                self.vector_field: vector,
                "source_name": resolved_source,
                "url": url,
                "content_hash": hashlib.sha256(chunk.encode()).hexdigest(),
                "chunk_index": index,
                "ingested_at": ingested_at,
            }
            for index, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]

        try:
            await self.collection.delete_many({"url": url})
            await self.collection.insert_many(docs)
        except Exception as exc:
            return IngestResult(
                status="error",
                url=url,
                chunk_count=0,
                message=str(exc),
            )

        return IngestResult(
            status="ok",
            url=url,
            chunk_count=len(docs),
            message="",
        )
