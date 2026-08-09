from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.knowledge_report import IngestJob, KnowledgeReport
from app.repositories.knowledge_report_repository import KnowledgeReportRepository
from app.services.knowledge_reports.service import (
    INGEST_JOB_STALE_AFTER,
    KnowledgeReportService,
)
from app.services.rag.ingest_service import IngestResult

ALLOWED_URL = "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1"
SECOND_ALLOWED_URL = "https://www.cdc.gov.tw/Category/Page/abc"
BLOCKED_URL = "https://www.google.com/search?q=test"


def _sample_report(**overrides) -> KnowledgeReport:
    now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    data = {
        "report_id": "KR-20260802-AB12",
        "line_user_id": "U_TEST",
        "status": "pending",
        "reason": "missing",
        "question": "高血壓飲食建議？",
        "user_note": None,
        "user_source_urls": [],
        "resolution": None,
        "reviewer_note": None,
        "ingest_job": None,
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return KnowledgeReport(**data)


@pytest.fixture
def mock_repo() -> MagicMock:
    repo = MagicMock(spec=KnowledgeReportRepository)
    repo.insert = AsyncMock(side_effect=lambda report: report)
    repo.update = AsyncMock(side_effect=lambda report: report)
    repo.find_by_report_id = AsyncMock(return_value=None)
    repo.list_by_line_user_id = AsyncMock(return_value=[])
    repo.list_by_statuses = AsyncMock(return_value=[])
    repo.count_by_statuses = AsyncMock(return_value=0)
    repo.delete_pending_or_reviewing_by_urls = AsyncMock(return_value=0)
    repo.start_ingest_job = AsyncMock(return_value=True)
    repo.finish_ingest_job = AsyncMock(return_value=True)
    return repo


@pytest.fixture
def mock_ingest() -> AsyncMock:
    ingest = MagicMock()
    ingest.ingest_url = AsyncMock(
        return_value=IngestResult(status="ok", url=ALLOWED_URL, chunk_count=2)
    )
    return ingest


@pytest.mark.asyncio
async def test_create_report(mock_repo: MagicMock):
    service = KnowledgeReportService(repository=mock_repo)

    report = await service.create(
        line_user_id="U_TEST",
        question="  問題  ",
        reason="outdated",
        user_note="  備註  ",
        user_source_urls=["https://example.com"],
    )

    assert report.line_user_id == "U_TEST"
    assert report.status == "pending"
    assert report.reason == "outdated"
    assert report.question == "問題"
    assert report.user_note == "備註"
    # report_id 的日期來自 _generate_report_id 的 datetime.now(UTC)，
    # 不是 _sample_report 的固定日期，所以要跟當下的 UTC 日期比對
    today_utc = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert report.report_id.startswith(f"KR-{today_utc}-")
    assert len(report.report_id.split("-")[-1]) == 4
    mock_repo.insert.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_for_user(mock_repo: MagicMock):
    expected = [_sample_report()]
    mock_repo.list_by_line_user_id.return_value = expected
    service = KnowledgeReportService(repository=mock_repo)

    reports = await service.list_for_user("U_TEST")

    assert reports == expected
    mock_repo.list_by_line_user_id.assert_awaited_once_with("U_TEST")


@pytest.mark.asyncio
async def test_list_for_admin_defaults_to_pending_and_reviewing(mock_repo: MagicMock):
    expected = [_sample_report()]
    mock_repo.list_by_statuses.return_value = expected
    mock_repo.count_by_statuses.return_value = 12
    service = KnowledgeReportService(repository=mock_repo)

    reports, total, status_counts = await service.list_for_admin(limit=20, offset=40)

    assert reports == expected
    assert total == 12
    assert set(status_counts) == {"pending", "reviewing"}
    mock_repo.list_by_statuses.assert_awaited_once_with(
        ["pending", "reviewing"], limit=20, offset=40
    )


@pytest.mark.asyncio
async def test_list_for_admin_with_status_filter(mock_repo: MagicMock):
    service = KnowledgeReportService(repository=mock_repo)

    await service.list_for_admin(status="resolved")

    mock_repo.list_by_statuses.assert_awaited_once_with(
        ["resolved"], limit=None, offset=0
    )
    # 篩選 resolved 時，佇列計數仍須回報 pending／reviewing 的實際筆數
    counted = [call.args[0] for call in mock_repo.count_by_statuses.await_args_list]
    assert counted == [["resolved"], ["pending"], ["reviewing"]]


@pytest.mark.asyncio
async def test_approve_registers_job_without_ingesting(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    mock_repo.find_by_report_id.return_value = _sample_report()
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)

    result = await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[ALLOWED_URL],
        resolution="已更新",
        reviewer_note="ok",
    )

    assert result.status == "reviewing"
    assert result.ingest_job is not None
    assert result.ingest_job.status == "running"
    assert result.ingest_job.started_at is not None
    assert result.ingest_job.finished_at is None
    assert result.ingest_job.selected_urls == [ALLOWED_URL]
    assert result.ingest_job.results == []
    # ingest 留給背景，approve 本身不得呼叫
    mock_ingest.ingest_url.assert_not_awaited()
    mock_repo.start_ingest_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_ingest_success(mock_repo: MagicMock, mock_ingest: AsyncMock):
    mock_repo.find_by_report_id.return_value = _sample_report()
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)
    await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[ALLOWED_URL],
    )

    result = await service.run_ingest("KR-20260802-AB12")

    assert result is not None
    assert result.status == "resolved"
    assert result.ingest_job is not None
    assert result.ingest_job.status == "succeeded"
    assert result.ingest_job.error is None
    assert result.ingest_job.finished_at is not None
    assert len(result.ingest_job.results) == 1
    mock_ingest.ingest_url.assert_awaited_once_with(ALLOWED_URL)


@pytest.mark.asyncio
async def test_run_ingest_failure_stays_reviewing(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    mock_repo.find_by_report_id.return_value = _sample_report()
    mock_ingest.ingest_url.return_value = IngestResult(
        status="error",
        url=ALLOWED_URL,
        chunk_count=0,
        message="scrape failed",
    )
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)
    await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[ALLOWED_URL],
    )

    result = await service.run_ingest("KR-20260802-AB12")

    assert result is not None
    assert result.status == "reviewing"
    assert result.ingest_job is not None
    assert result.ingest_job.status == "failed"
    assert result.ingest_job.error is not None
    assert "scrape failed" in result.ingest_job.error


@pytest.mark.asyncio
async def test_run_ingest_crash_marks_failed(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    mock_repo.find_by_report_id.return_value = _sample_report()
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)
    await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[ALLOWED_URL],
    )
    mock_ingest.ingest_url.side_effect = RuntimeError("boom")

    result = await service.run_ingest("KR-20260802-AB12")

    assert result is not None
    assert result.status == "reviewing"
    assert result.ingest_job is not None
    # 不得停留在 running，否則會被誤判為進行中而擋住重試
    assert result.ingest_job.status == "failed"
    assert "boom" in (result.ingest_job.error or "")
    assert result.ingest_job.finished_at is not None


@pytest.mark.asyncio
async def test_run_ingest_missing_report_returns_none(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)

    assert await service.run_ingest("KR-20260802-NONE") is None
    mock_ingest.ingest_url.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_rejects_running_job(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    mock_repo.find_by_report_id.return_value = _sample_report(
        status="reviewing",
        ingest_job=IngestJob(
            selected_urls=[ALLOWED_URL],
            status="running",
            started_at=datetime.now(timezone.utc),
        ),
    )
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)

    with pytest.raises(HTTPException) as exc:
        await service.approve(
            report_id="KR-20260802-AB12",
            selected_urls=[ALLOWED_URL],
        )

    assert exc.value.status_code == 409
    assert "running" in exc.value.detail
    mock_repo.start_ingest_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_retries_stale_running_job(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    stale_started_at = (
        datetime.now(timezone.utc) - INGEST_JOB_STALE_AFTER - timedelta(minutes=1)
    )
    mock_repo.find_by_report_id.return_value = _sample_report(
        status="reviewing",
        ingest_job=IngestJob(
            selected_urls=[ALLOWED_URL],
            status="running",
            started_at=stale_started_at,
        ),
    )
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)

    result = await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[ALLOWED_URL],
    )

    assert result.ingest_job is not None
    assert result.ingest_job.status == "running"
    assert result.ingest_job.started_at > stale_started_at


@pytest.mark.asyncio
async def test_approve_retries_failed_job(mock_repo: MagicMock, mock_ingest: AsyncMock):
    mock_repo.find_by_report_id.return_value = _sample_report(
        status="reviewing",
        ingest_job=IngestJob(
            selected_urls=[ALLOWED_URL],
            status="failed",
            error="scrape failed",
        ),
    )
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)

    result = await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[ALLOWED_URL],
    )

    assert result.ingest_job is not None
    assert result.ingest_job.status == "running"
    assert result.ingest_job.error is None


@pytest.mark.asyncio
async def test_approve_allows_legacy_job_without_status(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    # 本 change 之前寫入的紀錄沒有 status，須視為已結束、不擋重試
    mock_repo.find_by_report_id.return_value = _sample_report(
        status="reviewing",
        ingest_job=IngestJob(selected_urls=[ALLOWED_URL]),
    )
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)

    result = await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[ALLOWED_URL],
    )

    assert result.ingest_job is not None
    assert result.ingest_job.status == "running"


@pytest.mark.asyncio
async def test_approve_rejects_non_whitelist_url(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    mock_repo.find_by_report_id.return_value = _sample_report()
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)

    with pytest.raises(HTTPException) as exc:
        await service.approve(
            report_id="KR-20260802-AB12",
            selected_urls=[BLOCKED_URL],
        )

    assert exc.value.status_code == 400
    mock_ingest.ingest_url.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_not_found(mock_repo: MagicMock, mock_ingest: AsyncMock):
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)

    with pytest.raises(HTTPException) as exc:
        await service.approve(
            report_id="KR-20260802-NONE",
            selected_urls=[ALLOWED_URL],
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_approve_returns_409_when_registration_lost(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    # 併發下另一個請求先取得登記，本次條件式更新落空
    mock_repo.find_by_report_id.return_value = _sample_report()
    mock_repo.start_ingest_job.return_value = False
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)

    with pytest.raises(HTTPException) as exc:
        await service.approve(
            report_id="KR-20260802-AB12",
            selected_urls=[ALLOWED_URL],
        )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_approve_keeps_existing_notes_when_omitted(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    mock_repo.find_by_report_id.return_value = _sample_report(
        status="reviewing",
        reviewer_note="第一次審核備註",
        resolution="第一次結論",
        ingest_job=IngestJob(selected_urls=[ALLOWED_URL], status="failed"),
    )
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)

    result = await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[ALLOWED_URL],
    )

    # 重試不帶備註時不得清空前次寫入的內容
    assert result.reviewer_note == "第一次審核備註"
    assert result.resolution == "第一次結論"
    kwargs = mock_repo.start_ingest_job.await_args.kwargs
    assert kwargs["reviewer_note"] is None
    assert kwargs["resolution"] is None


@pytest.mark.asyncio
async def test_approve_overwrites_notes_when_provided(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    mock_repo.find_by_report_id.return_value = _sample_report(reviewer_note="舊備註")
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)

    result = await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[ALLOWED_URL],
        reviewer_note="新備註",
    )

    assert result.reviewer_note == "新備註"


@pytest.mark.asyncio
async def test_run_ingest_discards_result_when_report_changed(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    # 期間被拒絕或被重新 approve → 條件式寫回不命中
    mock_repo.find_by_report_id.return_value = _sample_report()
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)
    await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[ALLOWED_URL],
    )
    mock_repo.finish_ingest_job.return_value = False

    assert await service.run_ingest("KR-20260802-AB12") is None


@pytest.mark.asyncio
async def test_run_ingest_binds_write_to_its_own_job(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    mock_repo.find_by_report_id.return_value = _sample_report()
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)
    started = await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[ALLOWED_URL],
    )
    assert started.ingest_job is not None

    await service.run_ingest("KR-20260802-AB12")

    kwargs = mock_repo.finish_ingest_job.await_args.kwargs
    assert kwargs["started_at"] == started.ingest_job.started_at
    assert kwargs["report_status"] == "resolved"


@pytest.mark.asyncio
async def test_run_ingest_crash_keeps_partial_results(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    mock_repo.find_by_report_id.return_value = _sample_report()
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)
    await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[ALLOWED_URL, SECOND_ALLOWED_URL],
    )
    # 第一個成功、第二個炸掉
    mock_ingest.ingest_url.side_effect = [
        IngestResult(status="ok", url=ALLOWED_URL, chunk_count=2),
        RuntimeError("boom"),
    ]

    result = await service.run_ingest("KR-20260802-AB12")

    assert result is not None
    assert result.ingest_job is not None
    assert result.ingest_job.status == "failed"
    # 已完成的那筆不得被丟掉，否則重試會重複收錄
    assert [r.url for r in result.ingest_job.results] == [ALLOWED_URL]


@pytest.mark.asyncio
async def test_reject_blocked_while_ingest_running(mock_repo: MagicMock):
    mock_repo.find_by_report_id.return_value = _sample_report(
        status="reviewing",
        ingest_job=IngestJob(
            selected_urls=[ALLOWED_URL],
            status="running",
            started_at=datetime.now(timezone.utc),
        ),
    )
    service = KnowledgeReportService(repository=mock_repo)

    with pytest.raises(HTTPException) as exc:
        await service.reject(report_id="KR-20260802-AB12", reviewer_note="不要了")

    # 擋住才不會出現「已拒絕但內容已進向量庫」
    assert exc.value.status_code == 409
    mock_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_reject_allowed_after_ingest_stale(mock_repo: MagicMock):
    mock_repo.find_by_report_id.return_value = _sample_report(
        status="reviewing",
        ingest_job=IngestJob(
            selected_urls=[ALLOWED_URL],
            status="running",
            started_at=datetime.now(timezone.utc)
            - INGEST_JOB_STALE_AFTER
            - timedelta(minutes=1),
        ),
    )
    service = KnowledgeReportService(repository=mock_repo)

    result = await service.reject(report_id="KR-20260802-AB12", reviewer_note="不要了")

    assert result.status == "rejected"


@pytest.mark.asyncio
async def test_reject_report(mock_repo: MagicMock):
    mock_repo.find_by_report_id.return_value = _sample_report()
    service = KnowledgeReportService(repository=mock_repo)

    result = await service.reject(
        report_id="KR-20260802-AB12",
        reviewer_note="不符合",
        resolution="duplicate",
    )

    assert result.status == "rejected"
    assert result.reviewer_note == "不符合"
    assert result.resolution == "duplicate"
    mock_repo.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_from_web_fallback(mock_repo: MagicMock):
    service = KnowledgeReportService(repository=mock_repo)

    report = await service.create_from_web_fallback(
        question="  高血壓飲食？  ",
        urls=["  " + ALLOWED_URL + "  ", "", "  "],
        line_user_id="U_TEST",
    )

    assert report is not None
    assert report.status == "pending"
    assert report.reason == "missing"
    assert report.question == "高血壓飲食？"
    assert report.user_note == "auto:web-fallback"
    assert report.user_source_urls == [ALLOWED_URL]
    mock_repo.delete_pending_or_reviewing_by_urls.assert_awaited_once_with(
        [ALLOWED_URL]
    )
    mock_repo.insert.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_from_web_fallback_empty_urls_returns_none(mock_repo: MagicMock):
    service = KnowledgeReportService(repository=mock_repo)

    report = await service.create_from_web_fallback(
        question="問題",
        urls=["", "  "],
        line_user_id="U_TEST",
    )

    assert report is None
    mock_repo.delete_pending_or_reviewing_by_urls.assert_not_awaited()
    mock_repo.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_falls_back_to_user_source_urls(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    mock_repo.find_by_report_id.return_value = _sample_report(
        user_source_urls=[ALLOWED_URL]
    )
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)

    result = await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[],
    )

    assert result.ingest_job is not None
    assert result.ingest_job.selected_urls == [ALLOWED_URL]

    await service.run_ingest("KR-20260802-AB12")
    mock_ingest.ingest_url.assert_awaited_once_with(ALLOWED_URL)


@pytest.mark.asyncio
async def test_approve_empty_selected_and_user_urls_returns_400(
    mock_repo: MagicMock, mock_ingest: AsyncMock
):
    mock_repo.find_by_report_id.return_value = _sample_report(user_source_urls=[])
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)

    with pytest.raises(HTTPException) as exc:
        await service.approve(
            report_id="KR-20260802-AB12",
            selected_urls=[],
        )

    assert exc.value.status_code == 400
    mock_ingest.ingest_url.assert_not_awaited()
