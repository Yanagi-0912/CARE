from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_knowledge_report_service, require_admin_user
from app.models.knowledge_report import (
    ApproveKnowledgeReportRequest,
    KnowledgeReport,
    KnowledgeReportListResponse,
    RejectKnowledgeReportRequest,
)
from app.services.knowledge_reports.service import KnowledgeReportService

router = APIRouter(dependencies=[Depends(require_admin_user)])


@router.get(
    "",
    response_model=KnowledgeReportListResponse,
    summary="列出待審知識回報",
    description="取得 pending／reviewing 回報佇列；可選 status 篩選，預設兩者，依建立時間新到舊。",
)
async def list_knowledge_reports_for_admin(
    status: Optional[str] = Query(default=None),
    service: KnowledgeReportService = Depends(get_knowledge_report_service),
) -> KnowledgeReportListResponse:
    reports = await service.list_for_admin(status=status)
    return KnowledgeReportListResponse(reports=reports)


@router.post(
    "/{report_id}/approve",
    response_model=KnowledgeReport,
    summary="核准知識回報",
    description="核准回報並對選定白名單 URL 執行 ingest。",
)
async def approve_knowledge_report(
    report_id: str,
    body: ApproveKnowledgeReportRequest,
    service: KnowledgeReportService = Depends(get_knowledge_report_service),
) -> KnowledgeReport:
    return await service.approve(
        report_id=report_id,
        selected_urls=body.selected_urls,
        resolution=body.resolution,
        reviewer_note=body.reviewer_note,
    )


@router.post(
    "/{report_id}/reject",
    response_model=KnowledgeReport,
    summary="拒絕知識回報",
    description="拒絕回報，不執行 ingest。",
)
async def reject_knowledge_report(
    report_id: str,
    body: RejectKnowledgeReportRequest,
    service: KnowledgeReportService = Depends(get_knowledge_report_service),
) -> KnowledgeReport:
    return await service.reject(
        report_id=report_id,
        reviewer_note=body.reviewer_note,
        resolution=body.resolution,
    )
