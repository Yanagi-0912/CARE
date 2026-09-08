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
    link_checker=None,
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
            link_checker=link_checker,
        ),
        gemini_service,
    )


class FakeLinkChecker:
    """把指定網址判死，其餘判活。記錄被查過哪些網址。"""

    def __init__(self, dead=()):
        self._dead = set(dead)
        self.checked: list[str] = []

    async def alive(self, urls):
        urls = list(urls)
        self.checked.extend(urls)
        return {url: url not in self._dead for url in urls}


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
async def test_skips_hit_with_parser_divergent_url():
    """hit URL 為反斜線繞過字串時 normalize_url 回 None，不進 Document（本 change 核心迴歸）。"""
    web = FakeWebClient(
        hits=[
            WebSearchHit(
                title="偽裝網域",
                url="https://evil.com\\.gov.tw/x",
                description="足夠長的敘述文字，確保就算沒被擋也不會走到 scrape 分支。",
            )
        ],
    )
    svc, gemini = _make_service(web_client=web)

    result = await svc.answer("問題")

    assert result == rag_fail(RagFailCode.WEB_EMPTY)
    assert web.scrape_calls == []
    gemini.chat_model.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_document_url_is_normalized():
    """合法但帶 utm／大寫的 hit，Document.metadata["url"]（經來源清單顯示）是正規化字串。"""
    raw_url = "HTTPS://WWW.HPA.GOV.TW/htn?utm_source=line&nodeid=1"
    normalized_url = "https://www.hpa.gov.tw/htn?nodeid=1"
    web = FakeWebClient(
        hits=[WebSearchHit(title="國健署高血壓", url=raw_url)],
        pages={normalized_url: "控制血壓要規律量測與低鈉飲食。"},
    )
    svc, _ = _make_service(
        answer_content="根據網路資料，請規律量測血壓。",
        web_client=web,
    )

    result = await svc.answer("高血壓要注意什麼")

    assert f"[1] 網路：國健署高血壓：{normalized_url}" in result
    assert raw_url not in result
    # scrape 打的是正規化後的字串，不是原始大小寫／帶 utm 的字串
    assert web.scrape_calls == [normalized_url]


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


@pytest.fixture
def rag_sources_holder():
    """開一輪來源 holder（正式路徑由 message_handler 開場）。"""
    from app.core.rag_sources import (
        begin_request_rag_sources,
        reset_request_rag_sources,
    )

    token = begin_request_rag_sources()
    try:
        yield
    finally:
        reset_request_rag_sources(token)


@pytest.mark.asyncio
async def test_web_answer_exposes_structured_sources(rag_sources_holder):
    """走網搜的回答也要有結構化來源，否則卡片上一顆按鈕都不會有。

    卡片路徑會把內文的來源清單 strip 掉、改用按鈕呈現，來源只剩這一條路。
    """
    from app.core.rag_sources import get_request_rag_sources

    web = FakeWebClient(
        hits=[
            WebSearchHit(title="國健署高血壓", url="https://www.hpa.gov.tw/htn"),
            WebSearchHit(title="食藥署血壓藥", url="https://www.fda.gov.tw/bp"),
        ],
        pages={
            "https://www.hpa.gov.tw/htn": "控制血壓要規律量測與低鈉飲食。",
            "https://www.fda.gov.tw/bp": "血壓藥不可自行停藥。",
        },
    )
    svc, _ = _make_service(
        answer_content="根據公開網路資料，請規律量測血壓 [1]。",
        web_client=web,
    )

    result = await svc.answer("高血壓要注意什麼")

    refs = get_request_rag_sources()
    assert [r.index for r in refs] == [1, 2]
    assert [r.url for r in refs] == [
        "https://www.hpa.gov.tw/htn",
        "https://www.fda.gov.tw/bp",
    ]
    # 按鈕編號必須與文字清單一致，否則使用者點錯來源。
    for ref in refs:
        assert f"[{ref.index}] {ref.label}：{ref.url}" in result


@pytest.mark.asyncio
async def test_web_answer_without_usable_url_clears_sources(rag_sources_holder):
    """沒有可列的來源時要清空，不能留著上一次的殘值。"""
    from app.core.rag_sources import SourceRef, get_request_rag_sources
    from app.core.rag_sources import set_request_rag_sources

    set_request_rag_sources(
        [SourceRef(index=1, label="殘留", url="https://example.com/stale")]
    )

    assert WebSearchService._append_sources("答案本文。", []) == "答案本文。"
    assert get_request_rag_sources() == ()


# --- 來源網址存活檢查（link_check.py）---


@pytest.mark.asyncio
async def test_dead_url_is_dropped_from_web_sources():
    """網搜路徑判死的來源整筆不顯示：拿掉連結後只剩搜尋結果標題，
    對使用者驗證沒有價值（知識庫路徑的機構名才值得單獨保留）。"""
    dead = "https://sp1.hso.mohw.gov.tw/doctor/Often_question/type_detail.php"
    web = FakeWebClient(
        hits=[
            WebSearchHit(title="衛福部腳痛", url=dead, description="腳痛的常見原因說明。"),
            WebSearchHit(
                title="國健署",
                url="https://www.hpa.gov.tw/foot",
                description="足部保健的日常照護建議。",
            ),
        ],
    )
    svc, _ = _make_service(
        answer_content="請就醫評估。",
        web_client=web,
        link_checker=FakeLinkChecker(dead=[dead]),
    )

    result = await svc.answer("腳痛怎麼辦")

    assert dead not in result
    assert "[1] 網路：國健署：https://www.hpa.gov.tw/foot" in result


@pytest.mark.asyncio
async def test_dead_url_never_reaches_knowledge_report():
    """死鏈一旦經回報核准就會 ingest 進庫，成為之後每次引用的死連結。
    擋在入庫前，比事後在出口層一直降級它便宜。"""
    dead = "https://sp1.hso.mohw.gov.tw/gone"
    alive = "https://www.hpa.gov.tw/foot"
    web = FakeWebClient(
        hits=[
            WebSearchHit(title="衛福部", url=dead, description="腳痛的常見原因說明。"),
            WebSearchHit(title="國健署", url=alive, description="足部保健的照護建議。"),
        ],
    )
    reported = AsyncMock()
    svc, _ = _make_service(
        answer_content="請就醫評估。",
        web_client=web,
        on_web_fallback_success=reported,
        link_checker=FakeLinkChecker(dead=[dead]),
    )

    token = set_line_user_id("U123")
    try:
        await svc.answer("腳痛怎麼辦")
    finally:
        reset_line_user_id(token)

    reported.assert_awaited_once()
    assert reported.await_args.kwargs["urls"] == [alive]


@pytest.mark.asyncio
async def test_sources_unchanged_when_link_checker_absent():
    """未注入 checker 時行為與導入這個功能之前完全相同。"""
    url = "https://sp1.hso.mohw.gov.tw/gone"
    web = FakeWebClient(
        hits=[WebSearchHit(title="衛福部", url=url, description="腳痛的常見原因說明。")]
    )
    svc, _ = _make_service(answer_content="請就醫評估。", web_client=web)

    assert url in await svc.answer("腳痛怎麼辦")
