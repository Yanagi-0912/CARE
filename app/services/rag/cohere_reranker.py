"""RAG rerank：Cohere Rerank API，失敗或無 key 時以向量分數降級。"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx
from langchain_core.documents import Document

logger = logging.getLogger(__name__)

COHERE_RERANK_URL = "https://api.cohere.com/v2/rerank"


class Reranker(Protocol):
    async def rerank(
        self, query: str, docs: list[Document], *, top_n: int
    ) -> list[Document]: ...


class VectorScoreReranker:
    """依既有向量 score 排序後取 top_n（無 Cohere / API 失敗時的降級）。"""

    async def rerank(
        self, query: str, docs: list[Document], *, top_n: int
    ) -> list[Document]:
        del query  # unused; interface parity
        if not docs or top_n <= 0:
            return []
        ranked = sorted(
            docs,
            key=lambda d: float(d.metadata.get("score") or 0.0),
            reverse=True,
        )
        out: list[Document] = []
        for i, doc in enumerate(ranked[:top_n], start=1):
            meta = {**doc.metadata, "rerank_rank": i}
            out.append(Document(page_content=doc.page_content, metadata=meta))
        return out


async def _default_http_post(
    url: str,
    *,
    headers: dict[str, str],
    json: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=json)
        resp.raise_for_status()
        return resp.json()


class CohereReranker:
    """呼叫 Cohere Rerank API；任何失敗則降級為 VectorScoreReranker。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 10.0,
        http_post=_default_http_post,
        fallback: Reranker | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._http_post = http_post
        self._fallback = fallback or VectorScoreReranker()

    async def rerank(
        self, query: str, docs: list[Document], *, top_n: int
    ) -> list[Document]:
        if not docs or top_n <= 0:
            return []
        if not self._api_key:
            return await self._fallback.rerank(query, docs, top_n=top_n)

        documents = [d.page_content for d in docs]
        payload = {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_n": min(top_n, len(documents)),
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            data = await self._http_post(
                COHERE_RERANK_URL,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
            results = data.get("results") or []
            out: list[Document] = []
            for rank, item in enumerate(results, start=1):
                idx = int(item["index"])
                if idx < 0 or idx >= len(docs):
                    continue
                src = docs[idx]
                meta = {
                    **src.metadata,
                    "rerank_score": float(item.get("relevance_score") or 0.0),
                    "rerank_rank": rank,
                }
                out.append(Document(page_content=src.page_content, metadata=meta))
            if out:
                return out
            logger.warning("Cohere rerank returned empty results; falling back")
        except Exception:
            logger.exception("Cohere rerank failed; falling back to vector scores")
        return await self._fallback.rerank(query, docs, top_n=top_n)
