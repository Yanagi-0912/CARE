from __future__ import annotations

import random
import string
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from app.models.knowledge_report import (
    IngestJob,
    IngestJobResult,
    KnowledgeReport,
    KnowledgeReportReason,
)
from app.repositories.knowledge_report_repository import KnowledgeReportRepository
from app.services.rag.ingest_service import IngestService
from app.services.rag.whitelist import is_allowed_url


class KnowledgeReportService:
    def __init__(
        self,
        *,
        repository: KnowledgeReportRepository,
        ingest_service: Optional[IngestService] = None,
    ) -> None:
        self._repository = repository
        self._ingest_service = ingest_service

    @staticmethod
    def _generate_report_id(now: datetime | None = None) -> str:
        current = now or datetime.now(timezone.utc)
        suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        return f"KR-{current.strftime('%Y%m%d')}-{suffix}"

    async def create(
        self,
        *,
        line_user_id: str,
        question: str,
        reason: KnowledgeReportReason,
        user_note: str | None = None,
        user_source_urls: list[str] | None = None,
    ) -> KnowledgeReport:
        now = datetime.now(timezone.utc)
        report = KnowledgeReport(
            report_id=self._generate_report_id(now),
            line_user_id=line_user_id,
            status="pending",
            reason=reason,
            question=question.strip(),
            user_note=user_note.strip() if user_note else None,
            user_source_urls=list(user_source_urls or []),
            created_at=now,
            updated_at=now,
        )
        return await self._repository.insert(report)

    async def list_for_user(self, line_user_id: str) -> list[KnowledgeReport]:
        return await self._repository.list_by_line_user_id(line_user_id)

    async def approve(
        self,
        *,
        report_id: str,
        selected_urls: list[str],
        resolution: str | None = None,
        reviewer_note: str | None = None,
    ) -> KnowledgeReport:
        report = await self._repository.find_by_report_id(report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")

        if report.status in ("resolved", "rejected"):
            raise HTTPException(
                status_code=409,
                detail=f"Report already {report.status}",
            )

        normalized_urls = [url.strip() for url in selected_urls if url.strip()]
        if not normalized_urls:
            raise HTTPException(status_code=400, detail="selected_urls cannot be empty")

        for url in normalized_urls:
            if not is_allowed_url(url):
                raise HTTPException(
                    status_code=400,
                    detail=f"URL not in whitelist: {url}",
                )

        if self._ingest_service is None:
            raise HTTPException(status_code=503, detail="Ingest service not configured")

        now = datetime.now(timezone.utc)
        report.status = "reviewing"
        report.resolution = resolution
        report.reviewer_note = reviewer_note
        report.ingest_job = IngestJob(selected_urls=normalized_urls, results=[])
        report.updated_at = now
        await self._repository.update(report)

        results: list[IngestJobResult] = []
        all_ok = True
        for url in normalized_urls:
            ingest_result = await self._ingest_service.ingest_url(url)
            job_result = IngestJobResult(
                url=ingest_result.url,
                status=ingest_result.status,
                chunk_count=ingest_result.chunk_count,
                message=ingest_result.message,
            )
            results.append(job_result)
            if ingest_result.status != "ok":
                all_ok = False

        report.ingest_job.results = results
        report.updated_at = datetime.now(timezone.utc)

        if all_ok:
            report.status = "resolved"
            report.ingest_job.error = None
        else:
            report.status = "reviewing"
            failed = [r for r in results if r.status != "ok"]
            report.ingest_job.error = "; ".join(
                f"{r.url}: {r.status} ({r.message or 'failed'})" for r in failed
            )

        return await self._repository.update(report)

    async def reject(
        self,
        *,
        report_id: str,
        reviewer_note: str | None = None,
        resolution: str | None = None,
    ) -> KnowledgeReport:
        report = await self._repository.find_by_report_id(report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")

        if report.status in ("resolved", "rejected"):
            raise HTTPException(
                status_code=409,
                detail=f"Report already {report.status}",
            )

        now = datetime.now(timezone.utc)
        report.status = "rejected"
        report.reviewer_note = reviewer_note
        report.resolution = resolution
        report.updated_at = now
        return await self._repository.update(report)
