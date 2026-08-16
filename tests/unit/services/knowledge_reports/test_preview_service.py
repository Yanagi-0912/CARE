from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.knowledge_report import ContentPreview, ContentPreviewItem
from app.services.knowledge_reports.preview_service import ContentPreviewService
from app.services.rag.web_client import ScrapedPage

REPORT_ID = "KR-20260816-AB12"
URL_A = "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1"
URL_B = "https://www.cdc.gov.tw/Category/Page/abc"
BAD_URL = "https://evil.example.com/page"
CONTENT_A = "高血壓的成因與預防方式。"
HASH_A = hashlib.sha256(CONTENT_A.encode()).hexdigest()


def _repository() -> MagicMock:
    repository = MagicMock()
    repository.upsert_for_report = AsyncMock(side_effect=lambda preview: preview)
    repository.find_by_report_id = AsyncMock(return_value=None)
    repository.find_ready = AsyncMock(return_value=None)
    repository.finish = AsyncMock(return_value=True)
    return repository


def _web_client(pages: dict[str, ScrapedPage] | None = None) -> MagicMock:
    resolved = pages or {
        URL_A: ScrapedPage(text=CONTENT_A, final_url=URL_A, title="高血壓防治"),
        URL_B: ScrapedPage(text="糖尿病衛教內容。", final_url=URL_B, title="糖尿病"),
    }
    client = MagicMock()
    client.scrape_page = AsyncMock(side_effect=lambda url: resolved[url])
    return client


def _service(repository=None, web_client=None, **overrides) -> ContentPreviewService:
    kwargs = {
        "repository": repository or _repository(),
        "web_client": web_client or _web_client(),
        "ttl_minutes": 60,
        "max_urls": 5,
        "return_max_chars": 20000,
    }
    kwargs.update(overrides)
    return ContentPreviewService(**kwargs)


def _ready_preview(urls: list[str], *, now: datetime, preview_id: str = "PV-old") -> ContentPreview:
    return ContentPreview(
        preview_id=preview_id,
        report_id=REPORT_ID,
        status="ready",
        items=[
            ContentPreviewItem(
                url=url,
                status="ok",
                title="標題",
                content=CONTENT_A,
                content_hash=HASH_A,
                char_count=len(CONTENT_A),
            )
            for url in urls
        ],
        created_at=now,
        expires_at=now + timedelta(minutes=60),
    )


# --- 3.2 start(): 驗證與登記 -------------------------------------------------


@pytest.mark.asyncio
async def test_start_registers_running_preview_and_returns_normalized_urls():
    repository = _repository()
    service = _service(repository=repository)

    started = await service.start(report_id=REPORT_ID, urls=[URL_A])

    assert started.scheduled is True
    assert started.preview.status == "running"
    assert started.preview.report_id == REPORT_ID
    assert started.urls == [URL_A]
    repository.upsert_for_report.assert_awaited_once()
    saved = repository.upsert_for_report.await_args.args[0]
    assert saved.status == "running"


@pytest.mark.asyncio
async def test_start_does_not_scrape_in_the_request():
    """抓取必須發生在回應送出之後，start 本身 SHALL NOT 呼叫外部服務。"""
    web_client = _web_client()
    service = _service(web_client=web_client)

    await service.start(report_id=REPORT_ID, urls=[URL_A])

    web_client.scrape_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_rejects_all_non_whitelisted_urls_at_once():
    service = _service()

    with pytest.raises(HTTPException) as exc_info:
        await service.start(report_id=REPORT_ID, urls=[BAD_URL, "http://也不合法", URL_A])

    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    assert detail["code"] == "url_not_allowed"
    # 一次列出全部不合格網址，而不是遇到第一個就中止
    assert len(detail["invalid_urls"]) == 2


@pytest.mark.asyncio
async def test_start_rejects_when_url_count_exceeds_max():
    service = _service(max_urls=1)

    with pytest.raises(HTTPException) as exc_info:
        await service.start(report_id=REPORT_ID, urls=[URL_A, URL_B])

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "preview_too_many_urls"


@pytest.mark.asyncio
async def test_start_rejects_empty_url_list():
    service = _service()

    with pytest.raises(HTTPException) as exc_info:
        await service.start(report_id=REPORT_ID, urls=[])

    assert exc_info.value.status_code == 400


# --- 3.5 TTL 內冪等 ----------------------------------------------------------


@pytest.mark.asyncio
async def test_start_reuses_unexpired_ready_preview_with_same_urls():
    now = datetime.now(timezone.utc)
    repository = _repository()
    existing = _ready_preview([URL_A], now=now)
    repository.find_ready = AsyncMock(return_value=existing)
    web_client = _web_client()
    service = _service(repository=repository, web_client=web_client)

    started = await service.start(report_id=REPORT_ID, urls=[URL_A])

    assert started.preview.preview_id == "PV-old"
    # 不排背景工作、不重新登記，也就不會對外部服務發出新的抓取請求
    assert started.scheduled is False
    repository.upsert_for_report.assert_not_awaited()
    web_client.scrape_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_truncates_content_of_reused_preview():
    """沿用既有預覽時回傳的內容同樣受長度上限約束，POST 不該漏出全文。"""
    now = datetime.now(timezone.utc)
    repository = _repository()
    repository.find_ready = AsyncMock(return_value=_ready_preview([URL_A], now=now))
    service = _service(repository=repository, return_max_chars=3)

    started = await service.start(report_id=REPORT_ID, urls=[URL_A])

    assert started.preview.items[0].content == CONTENT_A[:3]
    assert started.preview.items[0].truncated is True


@pytest.mark.asyncio
async def test_start_refetches_when_url_set_differs():
    now = datetime.now(timezone.utc)
    repository = _repository()
    repository.find_ready = AsyncMock(return_value=_ready_preview([URL_A], now=now))
    service = _service(repository=repository)

    started = await service.start(report_id=REPORT_ID, urls=[URL_A, URL_B])

    assert started.scheduled is True
    assert started.preview.preview_id != "PV-old"


@pytest.mark.asyncio
async def test_start_with_force_refetches_and_issues_new_preview_id():
    now = datetime.now(timezone.utc)
    repository = _repository()
    repository.find_ready = AsyncMock(return_value=_ready_preview([URL_A], now=now))
    service = _service(repository=repository)

    started = await service.start(report_id=REPORT_ID, urls=[URL_A], force=True)

    assert started.scheduled is True
    assert started.preview.preview_id != "PV-old"
    repository.upsert_for_report.assert_awaited_once()


# --- 3.3 run(): 背景抓取 -----------------------------------------------------


@pytest.mark.asyncio
async def test_run_records_per_url_result_and_content_hash():
    repository = _repository()
    service = _service(repository=repository)

    await service.run(report_id=REPORT_ID, preview_id="PV-1", urls=[URL_A])

    finished = repository.finish.await_args.args[0]
    assert finished.status == "ready"
    item = finished.items[0]
    assert item.url == URL_A
    assert item.status == "ok"
    assert item.title == "高血壓防治"
    assert item.content == CONTENT_A
    assert item.content_hash == HASH_A
    assert item.char_count == len(CONTENT_A)


@pytest.mark.asyncio
async def test_run_marks_empty_content():
    web_client = _web_client({URL_A: ScrapedPage(text="   ", final_url=URL_A, title="")})
    repository = _repository()
    service = _service(repository=repository, web_client=web_client)

    await service.run(report_id=REPORT_ID, preview_id="PV-1", urls=[URL_A])

    finished = repository.finish.await_args.args[0]
    assert finished.items[0].status == "empty"
    assert finished.status == "failed"


@pytest.mark.asyncio
async def test_run_marks_error_when_scrape_raises_and_converges_to_failed():
    web_client = MagicMock()
    web_client.scrape_page = AsyncMock(side_effect=RuntimeError("firecrawl exploded"))
    repository = _repository()
    service = _service(repository=repository, web_client=web_client)

    await service.run(report_id=REPORT_ID, preview_id="PV-1", urls=[URL_A])

    finished = repository.finish.await_args.args[0]
    assert finished.items[0].status == "error"
    assert "firecrawl exploded" in finished.items[0].message
    # 例外絕不能讓預覽停在 running
    assert finished.status == "failed"


@pytest.mark.asyncio
async def test_run_continues_remaining_urls_after_one_fails():
    def _scrape(url: str) -> ScrapedPage:
        if url == URL_A:
            raise RuntimeError("boom")
        return ScrapedPage(text="糖尿病衛教內容。", final_url=URL_B, title="糖尿病")

    web_client = MagicMock()
    web_client.scrape_page = AsyncMock(side_effect=_scrape)
    repository = _repository()
    service = _service(repository=repository, web_client=web_client)

    await service.run(report_id=REPORT_ID, preview_id="PV-1", urls=[URL_A, URL_B])

    finished = repository.finish.await_args.args[0]
    assert [item.status for item in finished.items] == ["error", "ok"]


@pytest.mark.asyncio
async def test_run_marks_error_when_content_exceeds_size_limit():
    huge = "字" * (3 * 1024 * 1024)  # utf-8 下遠超過 8MB
    web_client = _web_client({URL_A: ScrapedPage(text=huge, final_url=URL_A, title="巨頁")})
    repository = _repository()
    service = _service(repository=repository, web_client=web_client)

    await service.run(report_id=REPORT_ID, preview_id="PV-1", urls=[URL_A])

    finished = repository.finish.await_args.args[0]
    assert finished.items[0].status == "error"
    assert finished.items[0].content == ""
    assert finished.status == "failed"


@pytest.mark.asyncio
async def test_run_writes_failed_when_repository_read_path_crashes():
    """未預期例外同樣 SHALL NOT 讓預覽停在 running。"""
    repository = _repository()
    web_client = MagicMock()
    # side_effect 不是 Exception 而是壞掉的回傳值，會在組裝 item 時炸開
    web_client.scrape_page = AsyncMock(return_value=object())
    service = _service(repository=repository, web_client=web_client)

    await service.run(report_id=REPORT_ID, preview_id="PV-1", urls=[URL_A])

    finished = repository.finish.await_args.args[0]
    assert finished.status == "failed"


@pytest.mark.asyncio
async def test_run_result_is_discarded_when_preview_was_superseded():
    """finish 綁 preview_id：期間被重新抓取取代的話本次結果丟棄。"""
    repository = _repository()
    repository.finish = AsyncMock(return_value=False)
    service = _service(repository=repository)

    applied = await service.run(report_id=REPORT_ID, preview_id="PV-1", urls=[URL_A])

    assert applied is False


# --- 3.4 get(): 截斷與逾期 ---------------------------------------------------


@pytest.mark.asyncio
async def test_get_truncates_content_and_flags_it():
    now = datetime.now(timezone.utc)
    long_content = "内" * 100
    stored = ContentPreview(
        preview_id="PV-1",
        report_id=REPORT_ID,
        status="ready",
        items=[
            ContentPreviewItem(
                url=URL_A,
                status="ok",
                title="標題",
                content=long_content,
                content_hash=hashlib.sha256(long_content.encode()).hexdigest(),
                char_count=len(long_content),
            )
        ],
        created_at=now,
        expires_at=now + timedelta(minutes=60),
    )
    repository = _repository()
    repository.find_by_report_id = AsyncMock(return_value=stored)
    service = _service(repository=repository, return_max_chars=10)

    preview = await service.get(REPORT_ID)

    assert preview is not None
    item = preview.items[0]
    assert item.content == "内" * 10
    assert item.truncated is True
    # 字數是截斷前的真實長度，雜湊仍是全文的雜湊——核准綁定的是伺服器端那份
    assert item.char_count == 100
    assert item.content_hash == stored.items[0].content_hash


@pytest.mark.asyncio
async def test_get_does_not_flag_truncated_when_within_limit():
    now = datetime.now(timezone.utc)
    repository = _repository()
    repository.find_by_report_id = AsyncMock(
        return_value=_ready_preview([URL_A], now=now)
    )
    service = _service(repository=repository)

    preview = await service.get(REPORT_ID)

    assert preview is not None
    assert preview.items[0].truncated is False
    assert preview.items[0].content == CONTENT_A


@pytest.mark.asyncio
async def test_get_returns_none_for_expired_preview():
    now = datetime.now(timezone.utc)
    expired = _ready_preview([URL_A], now=now - timedelta(minutes=120))
    repository = _repository()
    repository.find_by_report_id = AsyncMock(return_value=expired)
    service = _service(repository=repository)

    assert await service.get(REPORT_ID) is None


@pytest.mark.asyncio
async def test_get_returns_none_when_absent():
    service = _service()

    assert await service.get(REPORT_ID) is None


@pytest.mark.asyncio
async def test_start_reports_normalized_urls_on_the_preview():
    """預覽要帶回正規化後的 URL。

    呼叫端送出的字串可能帶追蹤參數或大寫主機名，之後所有環節（快照的鍵、
    核准送出的 selected_urls、向量庫的 url）都必須是同一份正規化字串；
    不回報的話呼叫端無從得知後端把它的網址改成了什麼，比對就永遠對不上。
    """
    repository = _repository()
    service = _service(repository=repository)

    started = await service.start(
        report_id=REPORT_ID,
        urls=["https://WWW.HPA.GOV.TW/Pages/Detail.aspx?nodeid=1&utm_source=line"],
    )

    assert started.preview.urls == started.urls
    assert started.preview.urls == [URL_A]


@pytest.mark.asyncio
async def test_run_keeps_the_url_list_on_the_finished_preview():
    repository = _repository()
    service = _service(repository=repository)

    await service.run(report_id=REPORT_ID, preview_id="PV-1", urls=[URL_A])

    finished = repository.finish.await_args.args[0]
    assert finished.urls == [URL_A]
