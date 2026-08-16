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

    async def ingest_url(
        self,
        url: str,
        *,
        source_name: str | None = None,
        default_source_name: str | None = None,
    ) -> IngestResult:
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

        return await self._write(
            url=url,
            normalized=normalized,
            final_norm=final_norm,
            text=text,
            source_name=source_name,
            default_source_name=default_source_name,
        )

    async def ingest_content(
        self,
        url: str,
        content: str,
        *,
        source_name: str | None = None,
        default_source_name: str | None = None,
    ) -> IngestResult:
        """以呼叫端提供的內容入庫，SHALL NOT 自行抓取。

        知識回報核准後的背景收錄走這一支：寫進向量庫的位元組就是 admin 在
        審核頁看過的那一份，approve 與 ingest 之間不再有抓取的時間差。
        """
        normalized = self.url_policy.normalize(url)
        if normalized is None or not self.url_policy.is_allowed(url):
            return IngestResult(
                status="rejected",
                url=url,
                chunk_count=0,
                message="URL not in whitelist",
            )

        if not content or not content.strip():
            return IngestResult(
                status="empty",
                url=url,
                chunk_count=0,
                message="Snapshot content is empty",
            )

        # 不重新抓取就沒有 final_url 可以二次驗證。這條路徑的重導向風險改由
        # 內容預覽承擔：admin 核准的是實際抓回來的那份內容本身。
        return await self._write(
            url=url,
            normalized=normalized,
            final_norm=None,
            text=content,
            source_name=source_name,
            default_source_name=default_source_name,
        )

    async def _resolve_source_name(
        self,
        *,
        normalized: str,
        delete_keys: list[str],
        source_name: str | None,
        default_source_name: str | None,
    ) -> str:
        """決定要寫入的 source_name。

        順序：呼叫端明確指定 → 該 URL 既有文件的名稱 → 呼叫端提供的預設值
        （例如抓取到的頁面標題）→ 空字串。中間這一層是本 change 要修的 bug：
        「這頁資料已過時」的處理路徑正是對既有策展 URL 重新收錄，沿用既有名稱
        才不會讓該來源在回答的參考清單裡只剩一串網址。

        呼叫端明確指定時直接採用、不去讀既有文件，營運才有辦法為既有 URL 改名。
        """
        if source_name:
            return source_name

        try:
            existing = await self.collection.find_one({"url": {"$in": delete_keys}})
        except Exception:
            # 沿用是「盡力而為」的優化，讀失敗不該讓整次收錄失敗
            logger.exception("讀取既有 source_name 失敗 url=%s", normalized)
            existing = None

        if isinstance(existing, dict):
            inherited = str(existing.get("source_name") or "").strip()
            if inherited:
                return inherited

        return default_source_name or ""

    async def _write(
        self,
        *,
        url: str,
        normalized: str,
        final_norm: str | None,
        text: str,
        source_name: str | None,
        default_source_name: str | None,
    ) -> IngestResult:
        """切塊、向量化並覆寫該 URL 的全部 chunk。ingest_url 與 ingest_content 共用。"""
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

        # 去重鍵放寬成 $in（design.md Decision 9）：既有文件是用正規化前的
        # 原字串存的，只用 normalized 刪除會讓舊 chunk 留在庫裡、同一頁變
        # 兩份。一次入庫涵蓋原字串／正規化字串／final_url 三種鍵（去重、
        # 去 None、保持穩定順序），之後就自然收斂，不需要 migration script。
        delete_keys: list[str] = []
        for key in (url, normalized, final_norm):
            if key is not None and key not in delete_keys:
                delete_keys.append(key)

        # 這道讀取必須排在下面的 delete_many 之前，否則要沿用的名稱已經被刪掉了
        resolved_source = await self._resolve_source_name(
            normalized=normalized,
            delete_keys=delete_keys,
            source_name=source_name,
            default_source_name=default_source_name,
        )

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
