"""Cohere / vector reranker 單元測試（client 以 DI 注入，禁止 monkey patch）。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from app.services.rag.cohere_reranker import (
    CohereReranker,
    VectorScoreReranker,
)


def _docs() -> list[Document]:
    return [
        Document(page_content="A", metadata={"id": "a", "score": 0.5, "url": "https://a"}),
        Document(page_content="B", metadata={"id": "b", "score": 0.9, "url": "https://b"}),
        Document(page_content="C", metadata={"id": "c", "score": 0.7, "url": "https://c"}),
    ]


@pytest.mark.asyncio
async def test_vector_score_reranker_orders_by_score_and_truncates():
    ranked = await VectorScoreReranker().rerank("q", _docs(), top_n=2)
    assert [d.page_content for d in ranked] == ["B", "C"]
    assert ranked[0].metadata["rerank_rank"] == 1
    assert ranked[1].metadata["rerank_rank"] == 2


@pytest.mark.asyncio
async def test_cohere_reranker_empty_docs_does_not_call_client():
    http_post = AsyncMock()
    reranker = CohereReranker(
        api_key="test-key",
        model="rerank-v4.0-pro",
        timeout_seconds=5,
        http_post=http_post,
    )
    assert await reranker.rerank("q", [], top_n=5) == []
    http_post.assert_not_called()


@pytest.mark.asyncio
async def test_cohere_reranker_reorders_by_api_and_sets_scores():
    async def http_post(url: str, *, headers: dict, json: dict, timeout: float) -> dict[str, Any]:
        assert "Authorization" in headers
        assert json["model"] == "rerank-v4.0-pro"
        assert json["top_n"] == 2
        # API returns indices into original documents list: prefer C then A
        return {
            "results": [
                {"index": 2, "relevance_score": 0.99},
                {"index": 0, "relevance_score": 0.88},
            ]
        }

    reranker = CohereReranker(
        api_key="test-key",
        model="rerank-v4.0-pro",
        timeout_seconds=5,
        http_post=http_post,
    )
    ranked = await reranker.rerank("q", _docs(), top_n=2)
    assert [d.page_content for d in ranked] == ["C", "A"]
    assert ranked[0].metadata["rerank_score"] == 0.99
    assert ranked[0].metadata["rerank_rank"] == 1
    assert ranked[1].metadata["rerank_score"] == 0.88


@pytest.mark.asyncio
async def test_cohere_reranker_falls_back_on_http_error():
    async def http_post(*_a, **_k):
        raise RuntimeError("cohere down")

    reranker = CohereReranker(
        api_key="test-key",
        model="rerank-v4.0-pro",
        timeout_seconds=5,
        http_post=http_post,
    )
    ranked = await reranker.rerank("q", _docs(), top_n=2)
    # fallback: vector score B, C
    assert [d.page_content for d in ranked] == ["B", "C"]
