import asyncio
import sys
import time
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
from app.services.rag.query_rewriter import RewrittenQuery
from app.core.user_language import SUPPORTED_LANGUAGES
from app.services.rag.web_client import WebSearchHit, WebSearchUnavailable
from app.services.rag.fail_messages import RagFailCode, rag_fail
from app.services.rag.web_search_service import (
    CITE_TOP_K,
    NO_ANSWER_MESSAGE,
    WEB_ANSWER_PREFIX,
    WebSearchService,
    web_answer_prefix,
)


class FakeWebClient:
    def __init__(
        self,
        hits=None,
        pages=None,
        search_error=None,
        scrape_error=None,
        hits_by_query=None,
    ):
        self.hits = hits or []
        # 依 query 回不同結果（中英兩路各自的命中）；沒列到的 query 退回 hits
        self.hits_by_query = hits_by_query or {}
        self.pages = pages or {}
        self.search_error = search_error
        self.scrape_error = scrape_error
        self.search_calls: list[str] = []
        self.search_domains: list = []
        self.scrape_calls: list[str] = []

    async def search(self, query: str, *, limit: int = 5, include_domains=None):
        self.search_calls.append(query)
        self.search_domains.append(include_domains)
        if self.search_error:
            raise self.search_error
        return self.hits_by_query.get(query, self.hits)[:limit]

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
    en_search_domains=(),
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
            en_search_domains=en_search_domains,
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
async def test_answer_reports_web_error_when_search_is_unavailable():
    """搜尋服務失敗（逾時、5xx）要回 WEB_ERROR，不是「找不到、請換個說法」；
    也不重搜——逾時再等一次 15 秒沒有意義。"""
    web = FakeWebClient(search_error=WebSearchUnavailable("timeout"))
    svc, gemini = _make_service(web_client=web)
    result = await svc.answer("問題")
    assert result == rag_fail(RagFailCode.WEB_ERROR)
    assert len(web.search_calls) == 1
    gemini.chat_model.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limit_reports_its_own_code_without_zh_retry():
    """429 當下立刻用原句重搜只會再吃一次 429；文案要說「太頻繁、等一下」。"""
    web = FakeWebClient(search_error=WebSearchUnavailable("http_429", status=429))
    svc, _ = _make_service(web_client=web)
    result = await svc.answer("問題")
    assert result == rag_fail(RagFailCode.WEB_RATE_LIMITED)
    assert len(web.search_calls) == 1


@pytest.mark.asyncio
async def test_unexpected_client_exception_is_also_a_web_error():
    """客戶端沒照契約丟 WebSearchUnavailable 的例外，一樣是「沒搜成」。"""
    web = FakeWebClient(search_error=RuntimeError("boom"))
    svc, _ = _make_service(web_client=web)
    result = await svc.answer("問題")
    assert result == rag_fail(RagFailCode.WEB_ERROR)


class _EnLegFailsWebClient(FakeWebClient):
    """英文那一路（帶 include_domains）限流，中文那一路正常。"""

    async def search(self, query: str, *, limit: int = 5, include_domains=None):
        if include_domains:
            self.search_calls.append(query)
            raise WebSearchUnavailable("http_429", status=429)
        return await super().search(query, limit=limit, include_domains=include_domains)


@pytest.mark.asyncio
async def test_one_leg_failing_still_answers_from_the_other_leg():
    web = _EnLegFailsWebClient(hits=[_hit("國健署", "https://www.hpa.gov.tw/a")])
    svc, _ = _make_service(
        answer_content="根據公開網路資料 [1]。",
        web_client=web,
        en_search_domains=_EN_DOMAINS,
    )
    result = await svc.answer("PGAD 是什麼病", search_queries=_PGAD_QUERIES)
    assert "https://www.hpa.gov.tw/a" in result
    assert len(web.search_calls) == 2  # zh + en，沒有 zh_retry


@pytest.mark.asyncio
async def test_genuine_empty_result_still_retries_and_reports_web_empty():
    """真的 0 筆才重搜、才說「找不到」——這條路的行為不變。"""
    web = FakeWebClient(hits=[])
    svc, _ = _make_service(web_client=web)
    result = await svc.answer("完全查不到的問題")
    assert result == rag_fail(RagFailCode.WEB_EMPTY)
    assert len(web.search_calls) == 2


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_rate_limit_message_differs_from_not_found_in_every_language(language):
    limited = rag_fail(RagFailCode.WEB_RATE_LIMITED, language)
    assert limited.startswith("[RAG_ERR:WEB_RATE_LIMITED] ")
    assert limited != rag_fail(RagFailCode.WEB_EMPTY, language)
    assert limited != rag_fail(RagFailCode.WEB_ERROR, language)
    assert "rag.fail." not in limited  # 每種語言都要有自己的文案，不能漏成 key


@pytest.mark.asyncio
async def test_answer_returns_no_answer_when_web_client_missing():
    svc, gemini = _make_service(web_client=None)
    result = await svc.answer("問題")
    assert result == rag_fail(RagFailCode.WEB_EMPTY)
    gemini.chat_model.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_logs_model_refuse_diagnostics(caplog):
    answer_content = "[NO_ANSWER] 我不知道這個問題的答案。"
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
    assert "matched_marker=[NO_ANSWER]" in refuse_logs[0]
    assert f"answer_preview={answer_content}" in refuse_logs[0]


@pytest.mark.asyncio
async def test_answer_returns_no_answer_when_model_cannot_answer():
    web = FakeWebClient(
        hits=[WebSearchHit(title="疾管署", url="https://www.cdc.gov.tw/w")],
        pages={"https://www.cdc.gov.tw/w": "流感疫苗建議。"},
    )
    svc, _ = _make_service(answer_content="[NO_ANSWER] 我不知道這個問題的答案。", web_client=web)
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
        answer_content="[NO_ANSWER] 我不知道這個問題的答案。",
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


# --- 查詢改寫後的中英兩路搜尋 ---

_PGAD_QUERIES = RewrittenQuery(
    kb_query="持續性性興奮症候群是什麼？",
    zh_terms="持續性性興奮症候群",
    en_terms="persistent genital arousal disorder",
)
_EN_DOMAINS = ("nih.gov", "medlineplus.gov")


def _hit(title, url):
    return WebSearchHit(
        title=title, url=url, description=f"{title}的說明文字，長度足夠不必抓全文。"
    )


@pytest.mark.asyncio
async def test_rewritten_queries_search_zh_and_en_legs():
    web = FakeWebClient()
    svc, _ = _make_service(web_client=web, en_search_domains=_EN_DOMAINS)

    await svc.answer("PGAD 是什麼病", search_queries=_PGAD_QUERIES)

    assert web.search_calls[:2] == [
        "持續性性興奮症候群 site:gov.tw",
        "persistent genital arousal disorder",
    ]
    assert web.search_domains[:2] == [None, _EN_DOMAINS]


@pytest.mark.asyncio
async def test_legs_are_interleaved_so_en_results_are_not_crowded_out():
    """罕見病：中文那路全是不相關內容時，英文那路的正解仍要擠進前 CITE_TOP_K。"""
    zh_hits = [
        _hit("多發性硬化症性功能障礙", "https://www.ntuh.gov.tw/a"),
        _hit("泌尿科常見問題", "https://sp1.hso.mohw.gov.tw/b"),
        _hit("精神科常見問題", "https://sp1.hso.mohw.gov.tw/c"),
    ]
    en_hits = [
        _hit("Persistent Genital Arousal Disorder", "https://pmc.ncbi.nlm.nih.gov/articles/PMC1/"),
        _hit("PGAD review", "https://pubmed.ncbi.nlm.nih.gov/2/"),
    ]
    web = FakeWebClient(
        hits_by_query={
            "持續性性興奮症候群 site:gov.tw": zh_hits,
            "persistent genital arousal disorder": en_hits,
        }
    )
    svc, _ = _make_service(
        answer_content="根據公開網路資料，PGAD 是一種罕見疾病 [2]。",
        web_client=web,
        en_search_domains=_EN_DOMAINS,
    )

    result = await svc.answer("PGAD 是什麼病", search_queries=_PGAD_QUERIES)

    assert "[1] 網路：多發性硬化症性功能障礙：https://www.ntuh.gov.tw/a" in result
    # 網址經正規化（去掉結尾斜線），與 test_document_url_is_normalized 同一條規則
    assert (
        "[2] 網路：Persistent Genital Arousal Disorder："
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC1" in result
    )
    assert "[3] 網路：泌尿科常見問題：https://sp1.hso.mohw.gov.tw/b" in result
    # 名額維持 CITE_TOP_K，不是兩路相加
    assert "pubmed.ncbi.nlm.nih.gov" not in result


@pytest.mark.asyncio
async def test_generation_and_report_use_original_question():
    """改寫只決定拿什麼去搜；回答的問題與知識回報仍是使用者的原句。"""
    web = FakeWebClient(
        hits_by_query={
            "persistent genital arousal disorder": [
                _hit("PGAD", "https://pmc.ncbi.nlm.nih.gov/articles/PMC1/")
            ]
        }
    )
    on_success = AsyncMock()
    svc, gemini = _make_service(
        answer_content="根據公開網路資料 [1]。",
        web_client=web,
        on_web_fallback_success=on_success,
        en_search_domains=_EN_DOMAINS,
    )
    token = set_line_user_id("U_LINE")
    try:
        await svc.answer("PGAD 是什麼病", search_queries=_PGAD_QUERIES)
    finally:
        reset_line_user_id(token)

    prompt = gemini.chat_model.ainvoke.await_args.args[0][0].content
    assert "PGAD 是什麼病" in prompt
    assert on_success.await_args.kwargs["question"] == "PGAD 是什麼病"


@pytest.mark.asyncio
async def test_en_leg_skipped_without_domains_or_terms():
    web = FakeWebClient()
    svc, _ = _make_service(web_client=web)  # 沒設英文網域
    await svc.answer("PGAD 是什麼病", search_queries=_PGAD_QUERIES)
    assert "persistent genital arousal disorder" not in web.search_calls

    web2 = FakeWebClient()
    svc2, _ = _make_service(web_client=web2, en_search_domains=_EN_DOMAINS)
    await svc2.answer(
        "低鈉飲食要注意什麼",
        search_queries=RewrittenQuery(kb_query="低鈉飲食注意事項", zh_terms="低鈉飲食"),
    )
    assert all(domains is None for domains in web2.search_domains)


@pytest.mark.asyncio
async def test_retries_with_original_question_when_no_docs():
    """Firecrawl 會隨機回 0 筆；兩路都沒有可用文件時，以原句再搜一次。"""
    web = FakeWebClient(
        hits_by_query={
            "PGAD 是什麼病 site:gov.tw": [_hit("衛福部說明", "https://www.mohw.gov.tw/x")]
        }
    )
    svc, _ = _make_service(
        answer_content="根據公開網路資料 [1]。",
        web_client=web,
        en_search_domains=_EN_DOMAINS,
    )

    result = await svc.answer("PGAD 是什麼病", search_queries=_PGAD_QUERIES)

    assert len(web.search_calls) == 3
    assert web.search_calls[-1] == "PGAD 是什麼病 site:gov.tw"
    assert "https://www.mohw.gov.tw/x" in result


@pytest.mark.asyncio
async def test_no_retry_when_first_round_has_docs():
    web = FakeWebClient(hits=[_hit("國健署", "https://www.hpa.gov.tw/a")])
    svc, _ = _make_service(answer_content="根據公開網路資料 [1]。", web_client=web)
    await svc.answer("高血壓要注意什麼")
    assert web.search_calls == ["高血壓要注意什麼 site:gov.tw"]


class _LimitRecordingWebClient(FakeWebClient):
    def __init__(self):
        super().__init__()
        self.limits: list = []

    async def search(self, query: str, *, limit: int = 5, include_domains=None):
        self.limits.append((include_domains, limit))
        return await super().search(
            query, limit=limit, include_domains=include_domains
        )


@pytest.mark.asyncio
async def test_en_leg_searches_only_cite_top_k_results():
    """英文那一路只取 CITE_TOP_K（3）筆；中文那一路維持 WEB_SEARCH_LIMIT（8）。"""
    web = _LimitRecordingWebClient()
    svc, _ = _make_service(web_client=web, en_search_domains=_EN_DOMAINS)

    await svc.answer("PGAD 是什麼病", search_queries=_PGAD_QUERIES)

    assert web.limits[:2] == [(None, 8), (_EN_DOMAINS, 3)]


@pytest.mark.asyncio
async def test_empty_model_output_is_treated_as_refusal():
    """模型回空字串時不能把預設文案（「抱歉，我目前找不到相關資料」）當答案送出。"""
    web = FakeWebClient(hits=[_hit("國健署", "https://www.hpa.gov.tw/a")])
    svc, _ = _make_service(answer_content="", web_client=web)

    result = await svc.answer("高血壓要注意什麼")

    assert result == rag_fail(RagFailCode.MODEL_REFUSE)


def _bare_hit(title, url):
    """snippet 短到會觸發 scrape 的命中（對照 `_hit` 的長 snippet）。"""
    return WebSearchHit(title=title, url=url, description="短")


@pytest.mark.asyncio
async def test_scrapes_run_in_parallel_not_one_after_another():
    """缺內文的候選要同時抓。

    單次 scrape 的逾時是 45 秒，逐一 await 時 n 筆抓不到就是 n×45 秒，整段
    網搜的 45 秒總逾時撐不到第二筆。這裡用「牆鐘時間接近一筆而不是三筆」
    來釘住並行，而不是只數呼叫次數——次數一樣，慢的才是問題。
    """
    delay = 0.05
    urls = [f"https://www.hpa.gov.tw/p{i}" for i in range(3)]
    web = FakeWebClient(
        hits=[_bare_hit(f"衛教{i}", url) for i, url in enumerate(urls)],
        pages={url: f"{url} 的內文夠長可以當作回答依據。" for url in urls},
    )

    async def slow_scrape(url: str) -> str:
        web.scrape_calls.append(url)
        await asyncio.sleep(delay)
        return web.pages.get(url, "")

    web.scrape = slow_scrape
    svc, _ = _make_service(web_client=web)

    started = time.perf_counter()
    await svc.answer("高血壓要注意什麼")
    elapsed = time.perf_counter() - started

    assert len(web.scrape_calls) == 3
    # 序列要 3×delay；抓一個 2 倍 delay 的門檻，慢機器上也不會假性失敗
    assert elapsed < delay * 2


@pytest.mark.asyncio
async def test_scrape_window_is_bounded_to_the_slots_that_exist():
    """只對前 CITE_TOP_K 筆發 scrape。

    序列版是「抓不到就再往下一筆」，最壞情況會把八筆命中全部抓過一遍、
    每筆各等一輪逾時。並行之後次數上限必須釘死，否則省下的是時間、賠掉的
    是額度。
    """
    urls = [f"https://www.hpa.gov.tw/p{i}" for i in range(8)]
    web = FakeWebClient(
        hits=[_bare_hit(f"衛教{i}", url) for i, url in enumerate(urls)],
        pages={},  # 全部抓不到
    )
    svc, _ = _make_service(web_client=web)

    await svc.answer("高血壓要注意什麼")

    assert len(web.scrape_calls) == CITE_TOP_K


@pytest.mark.asyncio
async def test_no_scrape_when_the_top_slots_already_have_usable_snippets():
    """前 CITE_TOP_K 名的 snippet 都夠用時，一次都不抓。

    這是實測打回來的迴歸：預抓視窗設得比 CITE_TOP_K 寬時，golden set 有一題
    會去抓排在第 4 名的 nhi.gov.tw PDF、卡滿 45 秒逾時，而前三名其實全都
    夠用。實測 153 筆候選裡 151 筆的 snippet ≥ 20 字，這條路才是常態。
    """
    good = [
        WebSearchHit(
            title=f"衛教{i}",
            url=f"https://www.hpa.gov.tw/ok{i}",
            description="這段描述明顯超過二十個字，足以直接當作回答依據，不必再抓全文。",
        )
        for i in range(3)
    ]
    slow_pdf = _bare_hit("公告", "https://media.nhi.gov.tw/md/dl-51926.pdf")
    web = FakeWebClient(hits=[*good, slow_pdf], pages={})
    svc, _ = _make_service(web_client=web)

    await svc.answer("高血壓要注意什麼")

    assert web.scrape_calls == []


@pytest.mark.asyncio
async def test_duplicate_url_is_scraped_only_once():
    """同一個網址在命中清單裡出現兩次時只抓一次。

    以前去重是等拿到內文才記進 seen，抓不到的網址不會進去，後面再出現就
    會再抓一次、再等一輪逾時。
    """
    url = "https://www.hpa.gov.tw/same"
    web = FakeWebClient(
        hits=[_bare_hit("衛教", url), _bare_hit("衛教（重複）", url)],
        pages={},
    )
    svc, _ = _make_service(web_client=web)

    await svc.answer("高血壓要注意什麼")

    assert web.scrape_calls == [url]


@pytest.mark.asyncio
async def test_short_snippet_survives_a_failed_scrape():
    """scrape 抓不到時退回原本的短 snippet，而不是整筆丟掉。"""
    url = "https://www.hpa.gov.tw/only-snippet"
    web = FakeWebClient(hits=[_bare_hit("衛教", url)], pages={})
    svc, _ = _make_service(web_client=web)

    result = await svc.answer("高血壓要注意什麼")

    assert url in result
