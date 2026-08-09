"""Cohere / vector reranker 單元測試（client 以 DI 注入，禁止 monkey patch）。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from app.services.rag.cohere_reranker import (
    CohereReranker,
    VectorScoreReranker,
    rerank_document_text,
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


def test_rerank_document_text_prefixes_title():
    doc = Document(
        page_content="幽門螺旋桿菌與胃癌風險有關。",
        metadata={"original_title": "捍「胃」健康 過年聚餐用公筷"},
    )
    assert rerank_document_text(doc) == (
        "主題：捍「胃」健康 過年聚餐用公筷\n內容：幽門螺旋桿菌與胃癌風險有關。"
    )


def test_rerank_document_text_falls_back_to_content_without_title():
    doc = Document(page_content="純內容", metadata={"original_title": None})
    assert rerank_document_text(doc) == "純內容"


@pytest.mark.asyncio
async def test_cohere_reranker_sends_title_prefixed_documents():
    captured: dict = {}

    async def fake_post(url, *, headers, json, timeout):
        captured["json"] = json
        return {"results": [{"index": 0, "relevance_score": 0.9}]}

    reranker = CohereReranker(
        api_key="k", model="rerank-v4.0-pro", http_post=fake_post
    )
    docs = [
        Document(page_content="內容A", metadata={"original_title": "標題A"}),
        Document(page_content="內容B", metadata={}),
    ]

    ranked = await reranker.rerank("q", docs, top_n=2)

    assert captured["json"]["documents"] == ["主題：標題A\n內容：內容A", "內容B"]
    # 回傳的 page_content 仍是原始 chunk_content，不含標題前綴
    assert ranked[0].page_content == "內容A"
