from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from app.services.rag.chunking import split_text_to_chunks
from app.services.rag.whitelist import UrlPolicy, default_url_policy

logger = logging.getLogger(__name__)

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
        url_policy: UrlPolicy | None = None,
    ) -> None:
        self.web_client = web_client
        self.embeddings = embeddings
        self.collection = collection
        self.text_field = text_field
        self.vector_field = vector_field
        self.vector_dim = vector_dim
        self.url_policy = url_policy or default_url_policy()

    async def ingest_url(self, url: str, *, source_name: str | None = None) -> IngestResult:
        normalized = self.url_policy.normalize(url)
        if normalized is None or not self.url_policy.is_allowed(url):
            return IngestResult(
                status="rejected",
                url=url,
                chunk_count=0,
                message="URL not in whitelist",
            )

        try:
            page = await self.web_client.scrape_page(url)
        except Exception as exc:
            return IngestResult(
                status="error",
                url=url,
                chunk_count=0,
                message=str(exc),
            )

        # 抓取後以最終 URL 二次驗證（design.md Decision 8）：事前檢查擋不住
        # Firecrawl 內部發生的重導向，一個合法的 gov.tw 網址可能被 302 到
        # 別處。這一步必須在切塊與向量化之前，通過白名單前絕不 embed。
        final_norm: str | None = None
        if page.final_url is not None:
            final_norm = self.url_policy.normalize(page.final_url)
            if final_norm is None or not self.url_policy.is_allowed(page.final_url):
                return IngestResult(
                    status="rejected",
                    url=url,
                    chunk_count=0,
                    message="重導向後的最終網址不在白名單內",
                )
        else:
            # Firecrawl 是黑箱，metadata 是我們唯一拿得到的證據；拿不到就
            # 一律拒絕會讓整條入庫在 Firecrawl 改版時全面停擺，所以以請求
            # URL 續行——這是明知的 fail-open，殘留風險由後續 change 2
            # 的 admin 內容預覽補（admin 看到的是實際抓回來的內容）。
            logger.info(
                "Firecrawl 未回報 final_url，以請求 URL 續行（fail-open）url=%s",
                url,
            )

        text = page.text
        if not text or not text.strip():
            return IngestResult(
                status="empty",
                url=url,
                chunk_count=0,
                message="Scrape returned empty content",
            )

        chunks = split_text_to_chunks(text)
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

        # final_url 與正規化後的請求 URL 不同時才額外寫欄位，讓營運事後查得出
        # 「這份 chunk 實際上抓自哪裡」；相同時沒有額外資訊，不寫。
        include_final_url = final_norm is not None and final_norm != normalized

        docs = []
        for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
            doc = {
                self.text_field: chunk,
                self.vector_field: vector,
                "source_name": resolved_source,
                "url": normalized,
                "content_hash": hashlib.sha256(chunk.encode()).hexdigest(),
                "chunk_index": index,
                "ingested_at": ingested_at,
            }
            if include_final_url:
                doc["final_url"] = final_norm
            docs.append(doc)

        # 去重鍵放寬成 $in（design.md Decision 9）：既有文件是用正規化前的
        # 原字串存的，只用 normalized 刪除會讓舊 chunk 留在庫裡、同一頁變
        # 兩份。一次入庫涵蓋原字串／正規化字串／final_url 三種鍵（去重、
        # 去 None、保持穩定順序），之後就自然收斂，不需要 migration script。
        delete_keys: list[str] = []
        for key in (url, normalized, final_norm):
            if key is not None and key not in delete_keys:
                delete_keys.append(key)

        try:
            await self.collection.delete_many({"url": {"$in": delete_keys}})
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
