from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import get_knowledge_report_service, require_admin_user
from app.models.knowledge_report import (
    ApproveKnowledgeReportRequest,
    KnowledgeReport,
    RejectKnowledgeReportRequest,
)
from app.services.knowledge_reports.service import KnowledgeReportService

router = APIRouter(dependencies=[Depends(require_admin_user)])


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
