from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.knowledge_report import KnowledgeReport
from app.repositories.knowledge_report_repository import KnowledgeReportRepository
from app.services.knowledge_reports.service import KnowledgeReportService
from app.services.rag.ingest_service import IngestResult

ALLOWED_URL = "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1"
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
async def test_approve_success(mock_repo: MagicMock, mock_ingest: AsyncMock):
    mock_repo.find_by_report_id.return_value = _sample_report()
    service = KnowledgeReportService(repository=mock_repo, ingest_service=mock_ingest)

    result = await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[ALLOWED_URL],
        resolution="已更新",
        reviewer_note="ok",
    )

    assert result.status == "resolved"
    assert result.ingest_job is not None
    assert result.ingest_job.error is None
    assert len(result.ingest_job.results) == 1
    mock_ingest.ingest_url.assert_awaited_once_with(ALLOWED_URL)
    assert mock_repo.update.await_count == 2


@pytest.mark.asyncio
async def test_approve_ingest_failure_stays_reviewing(
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

    result = await service.approve(
        report_id="KR-20260802-AB12",
        selected_urls=[ALLOWED_URL],
    )

    assert result.status == "reviewing"
    assert result.ingest_job is not None
    assert result.ingest_job.error is not None
    assert "scrape failed" in result.ingest_job.error


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
