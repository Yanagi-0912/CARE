import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

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

from langchain_core.messages import AIMessage

from app.services.rag.web_client import WebSearchHit
from app.services.rag.fail_messages import RagFailCode, rag_fail
from app.services.rag.web_search_service import (
    NO_ANSWER_MESSAGE,
    WEB_ANSWER_PREFIX,
    WebSearchService,
)


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


def _make_service(*, answer_content="網路回覆", web_client=None):
    gemini_service = MagicMock()
    gemini_service.chat_model = MagicMock()
    gemini_service.chat_model.ainvoke = AsyncMock(
        return_value=AIMessage(content=answer_content)
    )
    return (
        WebSearchService(gemini_service=gemini_service, web_client=web_client),
        gemini_service,
    )


@pytest.mark.asyncio
async def test_answer_uses_whitelisted_web_docs():
    web = FakeWebClient(
        hits=[
            WebSearchHit(title="國健署高血壓", url="https://www.hpa.gov.tw/htn"),
            WebSearchHit(title="論壇", url="https://forum.example/htn"),
        ],
        pages={
            "https://www.hpa.gov.tw/htn": "控制血壓要規律量測與低鈉飲食。"
        },
    )
    svc, gemini = _make_service(
        answer_content="根據網路資料，請規律量測血壓。",
        web_client=web,
    )
    result = await svc.answer("高血壓要注意什麼")
    assert WEB_ANSWER_PREFIX in result
    assert "根據網路資料，請規律量測血壓。" in result
    assert "[1] 網路：國健署高血壓：https://www.hpa.gov.tw/htn" in result
    assert "forum.example" not in result
    assert web.search_calls == ["高血壓要注意什麼 site:gov.tw"]
    gemini.chat_model.ainvoke.assert_awaited()


@pytest.mark.asyncio
async def test_answer_does_not_duplicate_existing_site_filter():
    web = FakeWebClient(
        hits=[WebSearchHit(title="食藥署", url="https://www.fda.gov.tw/x")],
        pages={"https://www.fda.gov.tw/x": "內容"},
    )
    svc, _ = _make_service(
        answer_content="根據公開網路資料，有相關說明。",
        web_client=web,
    )
    await svc.answer("胃痛 site:fda.gov.tw")
    assert web.search_calls == ["胃痛 site:fda.gov.tw"]


@pytest.mark.asyncio
async def test_answer_returns_no_answer_when_web_empty():
    web = FakeWebClient(hits=[], pages={})
    svc, gemini = _make_service(web_client=web)
    result = await svc.answer("完全查不到的問題")
    assert result == rag_fail(RagFailCode.WEB_EMPTY)
    assert "參考資料來源" not in result
    gemini.chat_model.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_degrades_when_web_client_raises():
    web = FakeWebClient(search_error=RuntimeError("boom"))
    svc, _ = _make_service(web_client=web)
    result = await svc.answer("問題")
    assert result == rag_fail(RagFailCode.WEB_EMPTY)


@pytest.mark.asyncio
async def test_answer_returns_no_answer_when_web_client_missing():
    svc, gemini = _make_service(web_client=None)
    result = await svc.answer("問題")
    assert result == rag_fail(RagFailCode.WEB_EMPTY)
    gemini.chat_model.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_returns_no_answer_when_model_cannot_answer():
    web = FakeWebClient(
        hits=[WebSearchHit(title="疾管署", url="https://www.cdc.gov.tw/w")],
        pages={"https://www.cdc.gov.tw/w": "流感疫苗建議。"},
    )
    svc, _ = _make_service(answer_content="我不知道這個問題的答案。", web_client=web)
    result = await svc.answer("流感疫苗")
    assert result == NO_ANSWER_MESSAGE
    assert result == rag_fail(RagFailCode.MODEL_REFUSE)
    assert "參考資料來源" not in result
