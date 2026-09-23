from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from app.services.rag.web_client import resolve_page_title
from app.services.rag.chunking import KB_CHUNKER_VERSION, split_kb_chunks
from app.services.rag.whitelist import UrlPolicy, default_url_policy

logger = logging.getLogger(__name__)

IngestStatus = Literal["ok", "rejected", "empty", "error"]

MISSING_TITLE_MESSAGE = "頁面沒有標題，無法依知識庫格式收錄"


def kb_embedding_input(title: str, chunk: str) -> str:
    """知識庫 chunk 的向量化輸入，與 CARE-data/main_pipeline.py 同一個格式。

    標題是整篇文章最強的主題訊號，而切塊後的內文不含標題。只 embed 內文的
    chunk 與 ETL 收進來的 chunk 不在同一種輸入上，分數也就不在同一個基準上比。
    """
    return f"主題：{title}\n內容：{chunk}"


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
            title=resolve_page_title(page),
            source_name=source_name,
            default_source_name=default_source_name,
        )

    async def ingest_content(
        self,
        url: str,
        content: str,
        *,
        title: str = "",
        source_name: str | None = None,
        default_source_name: str | None = None,
    ) -> IngestResult:
        """以呼叫端提供的內容入庫，SHALL NOT 自行抓取。

        知識回報核准後的背景收錄走這一支：寫進向量庫的位元組就是 admin 在
        審核頁看過的那一份，approve 與 ingest 之間不再有抓取的時間差。

        *title* 是頁面標題，會成為向量化輸入的「主題」與 original_title；
        沒有標題就不收（見 `_write`）。
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
            title=title,
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
        title: str,
        source_name: str | None,
        default_source_name: str | None,
    ) -> IngestResult:
        """切塊、向量化並覆寫該 URL 的全部 chunk。ingest_url 與 ingest_content 共用。

        寫出來的 chunk 與 CARE-data ETL 同格式：同一套切法（chunker_version）、
        同一種向量化輸入（`kb_embedding_input`）、帶 original_title、chunk_index
        從 1 起算。檢索時這兩個來源的 chunk 混在同一個索引裡互相比分數。
        """
        # 沒有標題就不收，與 ETL（CARE-data/scraper_api.py）同一條規則：向量化
        # 輸入會變成空白的「主題：」，BM25 的標題比對也對它無效。這個檢查排在
        # delete_many 之前，庫裡既有的這個 URL 不會因此被清掉。
        title = (title or "").strip()
        if not title:
            return IngestResult(
                status="empty",
                url=url,
                chunk_count=0,
                message=MISSING_TITLE_MESSAGE,
            )

        chunks = split_kb_chunks(text)
        if not chunks:
            return IngestResult(
                status="empty",
                url=url,
                chunk_count=0,
                message="No chunks after splitting",
            )

        try:
            vectors = await self.embeddings.aembed_documents(
                [kb_embedding_input(title, chunk) for chunk in chunks]
            )
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
        # chunk_index 從 1 起算，與 ETL 一致：kb_digest_service 以
        # chunk_index == 1 取每篇文章的第一片。
        for index, (chunk, vector) in enumerate(zip(chunks, vectors), start=1):
            doc = {
                self.text_field: chunk,
                self.vector_field: vector,
                "source_name": resolved_source,
                "url": normalized,
                "original_title": title,
                "content_hash": hashlib.sha256(chunk.encode()).hexdigest(),
                "chunk_index": index,
                "total_chunks": len(chunks),
                "chunker_version": KB_CHUNKER_VERSION,
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
