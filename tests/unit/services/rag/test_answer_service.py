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

    motor_asyncio_module.AsyncIOMotorClient = _DummyMotorClient
    motor_asyncio_module.AsyncIOMotorCollection = _DummyMotorCollection
    sys.modules["motor"] = motor_module
    sys.modules["motor.motor_asyncio"] = motor_asyncio_module

from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from app.services.rag import (
    CITE_TOP_K,
    NO_ANSWER_MESSAGE,
    RETRIEVAL_TOP_K,
    RagAnswerService,
)
from app.services.rag.web_client import WebSearchHit


class FakeWebClient:
    def __init__(self, hits=None, pages=None, search_error=None, scrape_error=None):
        self.hits = hits or []
        self.pages = pages or {}
        self.search_error = search_error
        self.scrape_error = scrape_error
        self.search_calls: list[str] = []
        self.scrape_calls: list[str] = []

    async def search(self, query: str, *, limit: int = 5):
        self.search_calls.append(query)
        if self.search_error:
            raise self.search_error
        return self.hits[:limit]

    async def scrape(self, url: str) -> str:
        self.scrape_calls.append(url)
        if self.scrape_error:
            raise self.scrape_error
        return self.pages.get(url, "")


def _make_service(*, docs, answer_content="RAG 回覆", web_client=None):
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
            web_client=web_client,
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
async def test_answer_puts_all_docs_in_prompt_but_cites_top_3_only():
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
        for i in range(1, RETRIEVAL_TOP_K + 1)
    ]
    svc, gemini_service, _retriever = _make_service(docs=docs)
    result = await svc.answer("測試問題")

    prompt = gemini_service.chat_model.ainvoke.await_args.args[0][0].content
    for i in range(1, RETRIEVAL_TOP_K + 1):
        assert f"{i}. 知識內容 {i}" in prompt

    for i in range(1, CITE_TOP_K + 1):
        assert f"[{i}] 來源 {i}：https://example.com/{i}" in result
    assert "來源 4" not in result
    assert "https://example.com/4" not in result


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
    # 空回應視為無法回答；無 web client 時降級為 NO_ANSWER_MESSAGE
    assert result == NO_ANSWER_MESSAGE
    assert "參考資料來源" not in result


@pytest.mark.asyncio
async def test_answer_returns_message_when_no_docs():
    svc, gemini_service, _retriever = _make_service(docs=[])
    result = await svc.answer("我有高血壓要注意什麼")
    assert result == NO_ANSWER_MESSAGE
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


def test_append_sources_web_kind_prefixes_network_label():
    docs = [
        Document(
            page_content="x",
            metadata={
                "source_name": "衛福部",
                "url": "https://www.mohw.gov.tw/x",
            },
        )
    ]
    result = RagAnswerService._append_sources(
        "答案正文", docs, source_kind="web"
    )
    assert "[1] 網路：衛福部：https://www.mohw.gov.tw/x" in result


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
async def test_answer_omits_kb_sources_when_model_cannot_answer(answer_content):
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
async def test_answer_uses_web_fallback_when_no_kb_docs():
    web = FakeWebClient(
        hits=[
            WebSearchHit(
                title="國健署高血壓",
                url="https://www.hpa.gov.tw/htn",
            ),
            WebSearchHit(title="論壇", url="https://forum.example/htn"),
        ],
        pages={
            "https://www.hpa.gov.tw/htn": "控制血壓要規律量測與低鈉飲食。"
        },
    )
    svc, gemini, _retriever = _make_service(
        docs=[],
        answer_content="根據網路資料，請規律量測血壓。",
        web_client=web,
    )
    result = await svc.answer("高血壓要注意什麼")
    assert "以下參考網路公開資料" in result
    assert "根據網路資料，請規律量測血壓。" in result
    assert "[1] 網路：國健署高血壓：https://www.hpa.gov.tw/htn" in result
    assert "forum.example" not in result
    assert web.search_calls == ["高血壓要注意什麼"]
    gemini.chat_model.ainvoke.assert_awaited()


@pytest.mark.asyncio
async def test_answer_uses_web_when_kb_cannot_answer():
    kb_docs = [
        Document(
            page_content="無關",
            metadata={"source_name": "KB", "url": "https://www.hpa.gov.tw/kb"},
        )
    ]
    web = FakeWebClient(
        hits=[WebSearchHit(title="疾管署", url="https://www.cdc.gov.tw/w")],
        pages={"https://www.cdc.gov.tw/w": "流感疫苗建議。"},
    )
    gemini_service = MagicMock()
    gemini_service.chat_model = MagicMock()
    gemini_service.chat_model.ainvoke = AsyncMock(
        side_effect=[
            AIMessage(content="我不知道這個問題的答案。"),
            AIMessage(content="建議依時程接種流感疫苗。"),
        ]
    )
    retriever = MagicMock()
    retriever.ainvoke = AsyncMock(return_value=kb_docs)
    svc = RagAnswerService(
        gemini_service=gemini_service,
        retriever=retriever,
        web_client=web,
    )
    result = await svc.answer("流感疫苗")
    assert "以下參考網路公開資料" in result
    assert "建議依時程接種流感疫苗。" in result
    assert "https://www.hpa.gov.tw/kb" not in result
    assert "[1] 網路：疾管署：https://www.cdc.gov.tw/w" in result


@pytest.mark.asyncio
async def test_answer_returns_no_answer_without_sources_when_web_fails():
    web = FakeWebClient(hits=[], pages={})
    svc, gemini, _ = _make_service(docs=[], web_client=web)
    result = await svc.answer("完全查不到的問題")
    assert result == NO_ANSWER_MESSAGE
    assert "參考資料來源" not in result
    gemini.chat_model.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_degrades_when_web_client_raises():
    web = FakeWebClient(search_error=RuntimeError("boom"))
    svc, _, _ = _make_service(docs=[], web_client=web)
    result = await svc.answer("問題")
    assert result == NO_ANSWER_MESSAGE
    assert "參考資料來源" not in result


@pytest.mark.asyncio
async def test_answer_does_not_mix_kb_and_web_sources():
    kb_docs = [
        Document(
            page_content="KB 內容",
            metadata={"source_name": "KB來源", "url": "https://www.hpa.gov.tw/kb"},
        )
    ]
    web = FakeWebClient(
        hits=[WebSearchHit(title="Web", url="https://www.mohw.gov.tw/w")],
        pages={"https://www.mohw.gov.tw/w": "Web 內容"},
    )
    gemini_service = MagicMock()
    gemini_service.chat_model = MagicMock()
    gemini_service.chat_model.ainvoke = AsyncMock(
        side_effect=[
            AIMessage(content="找不到相關資訊。"),
            AIMessage(content="這是網路答案。"),
        ]
    )
    retriever = MagicMock()
    retriever.ainvoke = AsyncMock(return_value=kb_docs)
    svc = RagAnswerService(
        gemini_service=gemini_service,
        retriever=retriever,
        web_client=web,
    )
    result = await svc.answer("混合測試")
    assert "KB來源" not in result
    assert "https://www.hpa.gov.tw/kb" not in result
    assert "網路：Web：https://www.mohw.gov.tw/w" in result


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
