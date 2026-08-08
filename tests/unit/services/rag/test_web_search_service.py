import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

if "motor.motor_asyncio" not in sys.modules:
    motor_module = types.ModuleType("motor")
    motor_asyncio_module = types.ModuleType("motor.motor_asyncio")

    class _DummyMotorClient:
        def __init__(self, *args, **kwargs):
            pass

        def __getitem__(self, name):
            return _DummyMotorDatabase()

    class _DummyMotorCollection:
        pass

    class _DummyMotorDatabase:
        def __getitem__(self, name):
            return _DummyMotorCollection()

    motor_asyncio_module.AsyncIOMotorClient = _DummyMotorClient
    motor_asyncio_module.AsyncIOMotorCollection = _DummyMotorCollection
    motor_asyncio_module.AsyncIOMotorDatabase = _DummyMotorDatabase
    sys.modules["motor"] = motor_module
    sys.modules["motor.motor_asyncio"] = motor_asyncio_module

from langchain_core.messages import AIMessage

from app.core.request_context import reset_line_user_id, set_line_user_id
from app.core.user_language import reset_request_language, set_request_language
from app.i18n.messages import t
from app.services.rag.web_client import WebSearchHit
from app.services.rag.fail_messages import RagFailCode, rag_fail
from app.services.rag.web_search_service import (
    NO_ANSWER_MESSAGE,
    WEB_ANSWER_PREFIX,
    WebSearchService,
    web_answer_prefix,
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


def _make_service(
    *,
    answer_content="網路回覆",
    web_client=None,
    on_web_fallback_success=None,
):
    gemini_service = MagicMock()
    gemini_service.chat_model = MagicMock()
    gemini_service.chat_model.ainvoke = AsyncMock(
        return_value=AIMessage(content=answer_content)
    )
    return (
        WebSearchService(
            gemini_service=gemini_service,
            web_client=web_client,
            on_web_fallback_success=on_web_fallback_success,
        ),
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
async def test_answer_prefers_search_description_without_scrape():
    web = FakeWebClient(
        hits=[
            WebSearchHit(
                title="不要用牙籤剔牙",
                url="https://www.mohw.gov.tw/toothpick",
                description="牙籤可能傷害牙齦與牙周組織，建議改用牙線清潔牙縫。",
            )
        ],
        pages={"https://www.mohw.gov.tw/toothpick": "完整內文不會被用到"},
    )
    svc, _ = _make_service(
        answer_content="根據公開網路資料，牙籤可能傷害牙齦。",
        web_client=web,
    )
    result = await svc.answer("牙籤會傷牙齒嗎")
    assert WEB_ANSWER_PREFIX in result
    assert "牙籤可能傷害牙齦" in result
    assert "https://www.mohw.gov.tw/toothpick" in result
    assert web.scrape_calls == []  # snippet 夠長就不 scrape


@pytest.mark.asyncio
async def test_answer_scrapes_when_description_too_short():
    web = FakeWebClient(
        hits=[
            WebSearchHit(
                title="牙痛",
                url="https://www.hpa.gov.tw/tooth",
                description="短",
            )
        ],
        pages={"https://www.hpa.gov.tw/tooth": "牙痛常見原因包含蛀牙與牙周病，建議盡快就醫。"},
    )
    svc, _ = _make_service(
        answer_content="根據公開網路資料，牙痛可能與蛀牙有關。",
        web_client=web,
    )
    result = await svc.answer("我有牙痛")
    assert WEB_ANSWER_PREFIX in result
    assert web.scrape_calls == ["https://www.hpa.gov.tw/tooth"]
    assert "https://www.hpa.gov.tw/tooth" in result


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
async def test_answer_logs_model_refuse_diagnostics(caplog):
    answer_content = "我不知道這個問題的答案。"
    web = FakeWebClient(
        hits=[WebSearchHit(title="疾管署", url="https://www.cdc.gov.tw/w")],
        pages={"https://www.cdc.gov.tw/w": "流感疫苗建議。"},
    )
    svc, _ = _make_service(answer_content=answer_content, web_client=web)
    with caplog.at_level("INFO"):
        result = await svc.answer("流感疫苗")
    assert result == NO_ANSWER_MESSAGE
    refuse_logs = [
        rec.getMessage()
        for rec in caplog.records
        if "rag_fail code=MODEL_REFUSE" in rec.getMessage()
    ]
    assert len(refuse_logs) == 1
    assert "matched_marker=不知道" in refuse_logs[0]
    assert f"answer_preview={answer_content}" in refuse_logs[0]


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


@pytest.mark.asyncio
async def test_answer_localizes_web_source_label_and_prefix():
    web = FakeWebClient(
        hits=[
            WebSearchHit(title="HPA hypertension", url="https://www.hpa.gov.tw/htn"),
        ],
        pages={"https://www.hpa.gov.tw/htn": "Monitor blood pressure regularly."},
    )
    svc, _ = _make_service(
        answer_content="Based on public web sources, monitor BP regularly.",
        web_client=web,
    )
    token = set_request_language("en")
    try:
        result = await svc.answer("hypertension tips")
    finally:
        reset_request_language(token)

    assert web_answer_prefix("en") in result
    assert t("rag.web_source_label", "en") == "Web"
    assert "[1] Web：HPA hypertension：https://www.hpa.gov.tw/htn" in result
    assert t("agent.sources_heading", "en") in result
    assert "網路：" not in result
    assert WEB_ANSWER_PREFIX not in result  # 英文請求不應出現繁中前綴


@pytest.mark.asyncio
async def test_answer_success_calls_create_from_web_fallback():
    web = FakeWebClient(
        hits=[
            WebSearchHit(title="國健署高血壓", url="https://www.hpa.gov.tw/htn"),
            WebSearchHit(title="論壇", url="https://forum.example/htn"),
        ],
        pages={
            "https://www.hpa.gov.tw/htn": "控制血壓要規律量測與低鈉飲食。"
        },
    )
    on_success = AsyncMock()
    svc, _ = _make_service(
        answer_content="根據網路資料，請規律量測血壓。",
        web_client=web,
        on_web_fallback_success=on_success,
    )
    token = set_line_user_id("U_LINE")
    try:
        result = await svc.answer("高血壓要注意什麼")
    finally:
        reset_line_user_id(token)

    assert WEB_ANSWER_PREFIX in result
    on_success.assert_awaited_once_with(
        question="高血壓要注意什麼",
        urls=["https://www.hpa.gov.tw/htn"],
        line_user_id="U_LINE",
    )


@pytest.mark.asyncio
async def test_answer_web_empty_does_not_create_knowledge_report():
    web = FakeWebClient(hits=[], pages={})
    on_success = AsyncMock()
    svc, _ = _make_service(web_client=web, on_web_fallback_success=on_success)
    token = set_line_user_id("U_LINE")
    try:
        result = await svc.answer("完全查不到的問題")
    finally:
        reset_line_user_id(token)

    assert result == rag_fail(RagFailCode.WEB_EMPTY)
    on_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_model_refuse_does_not_create_knowledge_report():
    web = FakeWebClient(
        hits=[WebSearchHit(title="疾管署", url="https://www.cdc.gov.tw/w")],
        pages={"https://www.cdc.gov.tw/w": "流感疫苗建議。"},
    )
    on_success = AsyncMock()
    svc, _ = _make_service(
        answer_content="我不知道這個問題的答案。",
        web_client=web,
        on_web_fallback_success=on_success,
    )
    token = set_line_user_id("U_LINE")
    try:
        result = await svc.answer("流感疫苗")
    finally:
        reset_line_user_id(token)

    assert result == rag_fail(RagFailCode.MODEL_REFUSE)
    on_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_missing_line_user_id_skips_create_but_returns_answer():
    web = FakeWebClient(
        hits=[
            WebSearchHit(title="國健署高血壓", url="https://www.hpa.gov.tw/htn"),
        ],
        pages={
            "https://www.hpa.gov.tw/htn": "控制血壓要規律量測與低鈉飲食。"
        },
    )
    on_success = AsyncMock()
    svc, _ = _make_service(
        answer_content="根據網路資料，請規律量測血壓。",
        web_client=web,
        on_web_fallback_success=on_success,
    )
    result = await svc.answer("高血壓要注意什麼")

    assert WEB_ANSWER_PREFIX in result
    assert "https://www.hpa.gov.tw/htn" in result
    on_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_create_failure_still_returns_answer():
    web = FakeWebClient(
        hits=[
            WebSearchHit(title="國健署高血壓", url="https://www.hpa.gov.tw/htn"),
        ],
        pages={
            "https://www.hpa.gov.tw/htn": "控制血壓要規律量測與低鈉飲食。"
        },
    )
    on_success = AsyncMock(side_effect=RuntimeError("mongo down"))
    svc, _ = _make_service(
        answer_content="根據網路資料，請規律量測血壓。",
        web_client=web,
        on_web_fallback_success=on_success,
    )
    token = set_line_user_id("U_LINE")
    try:
        result = await svc.answer("高血壓要注意什麼")
    finally:
        reset_line_user_id(token)

    assert WEB_ANSWER_PREFIX in result
    assert "https://www.hpa.gov.tw/htn" in result
    on_success.assert_awaited_once()
