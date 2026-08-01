import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# 測試環境可能未安裝 motor，先提供最小 stub 避免 import 失敗
if "motor.motor_asyncio" not in sys.modules:
    motor_module = types.ModuleType("motor")
    motor_asyncio_module = types.ModuleType("motor.motor_asyncio")

    class _DummyMotorClient:
        pass

    class _DummyMotorCollection:
        pass

    class _DummyMotorDatabase:
        pass

    motor_asyncio_module.AsyncIOMotorClient = _DummyMotorClient
    motor_asyncio_module.AsyncIOMotorCollection = _DummyMotorCollection
    motor_asyncio_module.AsyncIOMotorDatabase = _DummyMotorDatabase
    sys.modules["motor"] = motor_module
    sys.modules["motor.motor_asyncio"] = motor_asyncio_module

from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from app.services.rag import (
    CITE_TOP_K,
    NO_ANSWER_MESSAGE,
    NO_HITS_MESSAGE,
    RERANK_TOP_N,
    RagAnswerService,
)
from app.services.rag.cohere_reranker import VectorScoreReranker


def _make_service(*, docs, answer_content="RAG 回覆", reranker=None, rerank_top_n=RERANK_TOP_N):
    gemini_service = MagicMock()
    gemini_service.chat_model = MagicMock()
    gemini_service.chat_model.ainvoke = AsyncMock(
        return_value=AIMessage(content=answer_content)
    )

    retriever = MagicMock()
    retriever.ainvoke = AsyncMock(return_value=docs)

    return (
        RagAnswerService(
            gemini_service=gemini_service,
            retriever=retriever,
            reranker=reranker or VectorScoreReranker(),
            rerank_top_n=rerank_top_n,
        ),
        gemini_service,
        retriever,
    )


@pytest.mark.asyncio
async def test_answer_uses_docs_to_build_rag_prompt():
    docs = [
        Document(
            page_content="高血壓建議低鈉飲食",
            metadata={
                "id": "1",
                "score": 0.9,
                "source_name": "衛福部闢謠網站",
                "url": "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=5020&pid=19922",
            },
        ),
        Document(
            page_content="規律量血壓",
            metadata={
                "id": "2",
                "score": 0.8,
                "source_name": "衛福部闢謠網站",
                "url": "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=5020&pid=19922",
            },
        ),
    ]
    svc, gemini_service, retriever = _make_service(docs=docs)
    result = await svc.answer("我有高血壓要注意什麼")

    assert "RAG 回覆" in result
    assert "資料來源：" in result
    assert "衛福部闢謠網站" in result
    retriever.ainvoke.assert_awaited_once_with("我有高血壓要注意什麼")

    prompt = gemini_service.chat_model.ainvoke.await_args.args[0][0].content
    assert "高血壓建議低鈉飲食" in prompt
    assert "規律量血壓" in prompt


@pytest.mark.asyncio
async def test_answer_puts_rerank_top_n_in_prompt_but_cites_top_3_only():
    docs = [
        Document(
            page_content=f"知識內容 {i}",
            metadata={
                "id": str(i),
                "score": 1.0 - i * 0.05,
                "source_name": f"來源 {i}",
                "url": f"https://example.com/{i}",
            },
        )
        for i in range(1, 12)
    ]
    svc, gemini_service, _retriever = _make_service(docs=docs, rerank_top_n=RERANK_TOP_N)
    result = await svc.answer("測試問題")

    prompt = gemini_service.chat_model.ainvoke.await_args.args[0][0].content
    for i in range(1, RERANK_TOP_N + 1):
        assert f"{i}. 知識內容 {i}" in prompt
    assert f"{RERANK_TOP_N + 1}. 知識內容" not in prompt

    for i in range(1, CITE_TOP_K + 1):
        assert f"[{i}] 來源 {i}：https://example.com/{i}" in result
    assert "來源 4" not in result
    assert "https://example.com/4" not in result


@pytest.mark.asyncio
async def test_answer_uses_reranker_order_for_prompt_and_citations():
    docs = [
        Document(
            page_content="低分但應排後",
            metadata={
                "id": "1",
                "score": 0.99,
                "source_name": "來源A",
                "url": "https://example.com/a",
            },
        ),
        Document(
            page_content="精排第一",
            metadata={
                "id": "2",
                "score": 0.1,
                "source_name": "來源B",
                "url": "https://example.com/b",
            },
        ),
    ]

    class FixedReranker:
        async def rerank(self, query, docs, *, top_n):
            del query, top_n
            return [docs[1], docs[0]]

    svc, gemini_service, _retriever = _make_service(
        docs=docs, reranker=FixedReranker(), rerank_top_n=2
    )
    result = await svc.answer("測試")
    prompt = gemini_service.chat_model.ainvoke.await_args.args[0][0].content
    assert prompt.index("精排第一") < prompt.index("低分但應排後")
    assert "[1] 來源B：https://example.com/b" in result
    assert "[2] 來源A：https://example.com/a" in result


@pytest.mark.asyncio
async def test_answer_skips_reranker_when_no_docs():
    reranker = MagicMock()
    reranker.rerank = AsyncMock()
    svc, gemini_service, _retriever = _make_service(docs=[], reranker=reranker)
    result = await svc.answer("我有高血壓要注意什麼")
    assert result == NO_HITS_MESSAGE
    gemini_service.chat_model.ainvoke.assert_not_awaited()
    reranker.rerank.assert_not_awaited()


@pytest.mark.parametrize("model_text", [""], ids=["empty_str"])
@pytest.mark.asyncio
async def test_answer_uses_default_message_when_model_returns_empty_text(model_text):
    docs = [
        Document(
            page_content="高血壓建議低鈉飲食",
            metadata={"id": "1", "score": 0.9, "source_name": None, "url": None},
        )
    ]
    svc, _gemini, _retriever = _make_service(docs=docs, answer_content=model_text)
    result = await svc.answer("我有高血壓要注意什麼")
    assert result == NO_ANSWER_MESSAGE
    assert "參考資料來源" not in result


@pytest.mark.asyncio
async def test_answer_returns_hits_message_when_no_docs():
    svc, gemini_service, _retriever = _make_service(docs=[])
    result = await svc.answer("我有高血壓要注意什麼")
    assert result == NO_HITS_MESSAGE
    gemini_service.chat_model.ainvoke.assert_not_awaited()


def test_append_sources_renumbers_after_skipping_missing_and_duplicate_urls():
    docs = [
        Document(page_content="a", metadata={"source_name": "缺網址", "url": ""}),
        Document(
            page_content="b",
            metadata={
                "source_name": "國健署",
                "url": "https://www.hpa.gov.tw/a",
            },
        ),
        Document(
            page_content="c",
            metadata={
                "source_name": "重複",
                "url": "https://www.hpa.gov.tw/a",
            },
        ),
        Document(
            page_content="d",
            metadata={
                "source_name": "疾管署",
                "url": "https://www.cdc.gov.tw/b",
            },
        ),
    ]
    result = RagAnswerService._append_sources("答案正文", docs)
    assert "參考資料來源：" in result
    assert "[1] 國健署：https://www.hpa.gov.tw/a" in result
    assert "[2] 疾管署：https://www.cdc.gov.tw/b" in result
    assert "[3]" not in result
    assert "缺網址" not in result


@pytest.mark.parametrize(
    "answer_content",
    [
        "我不知道這個問題的答案。",
        "根據現有資料無法提供建議。",
        "未找到足夠資訊。",
        "找不到相關的衛教說明。",
    ],
)
@pytest.mark.asyncio
async def test_answer_returns_no_answer_when_model_cannot_answer(answer_content):
    docs = [
        Document(
            page_content="無關片段",
            metadata={
                "source_name": "國健署",
                "url": "https://www.hpa.gov.tw/x",
            },
        )
    ]
    svc, _gemini, _retriever = _make_service(
        docs=docs, answer_content=answer_content
    )
    result = await svc.answer("某個冷門問題")
    assert result == NO_ANSWER_MESSAGE
    assert "參考資料來源" not in result
    assert "https://www.hpa.gov.tw/x" not in result


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("正常可回答的衛教內容", False),
        ("我不知道", True),
        ("無法提供相關資訊", True),
        ("", True),
        ("   ", True),
    ],
)
def test_is_cannot_answer_heuristic(text, expected):
    assert RagAnswerService._is_cannot_answer(text) is expected


@pytest.mark.asyncio
async def test_answer_raises_when_retriever_fails():
    gemini_service = MagicMock()
    gemini_service.chat_model = MagicMock()
    gemini_service.chat_model.ainvoke = AsyncMock()
    retriever = MagicMock()
    retriever.ainvoke = AsyncMock(side_effect=RuntimeError("search failed"))

    svc = RagAnswerService(gemini_service=gemini_service, retriever=retriever)
    with pytest.raises(RuntimeError, match="search failed"):
        await svc.answer("我有高血壓要注意什麼")
