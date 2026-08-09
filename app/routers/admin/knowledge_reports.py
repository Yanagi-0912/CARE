from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from app.dependencies import get_knowledge_report_service, require_admin_user
from app.models.knowledge_report import (
    ApproveKnowledgeReportRequest,
    KnowledgeReport,
    KnowledgeReportListResponse,
    KnowledgeReportStatus,
    RejectKnowledgeReportRequest,
)
from app.services.knowledge_reports.service import KnowledgeReportService

router = APIRouter(dependencies=[Depends(require_admin_user)])


@router.get(
    "",
    response_model=KnowledgeReportListResponse,
    summary="列出待審知識回報",
    description=(
        "取得 pending／reviewing 回報佇列；可選 status 篩選，預設兩者，依建立時間新到舊。"
        "支援 limit／offset 分頁，回應含符合條件的總筆數。"
    ),
)
async def list_knowledge_reports_for_admin(
    status: Optional[KnowledgeReportStatus] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: KnowledgeReportService = Depends(get_knowledge_report_service),
) -> KnowledgeReportListResponse:
    reports, total, status_counts = await service.list_for_admin(
        status=status, limit=limit, offset=offset
    )
    return KnowledgeReportListResponse(
        reports=reports,
        total=total,
        limit=limit,
        offset=offset,
        status_counts=status_counts,
    )


@router.post(
    "/{report_id}/approve",
    response_model=KnowledgeReport,
    summary="核准知識回報",
    description=(
        "核准回報並排入 ingest；驗證同步完成後立即回傳 reviewing／running，"
        "實際 ingest 於回應後在背景執行。再次呼叫可重試失敗的 ingest。"
    ),
)
async def approve_knowledge_report(
    report_id: str,
    body: ApproveKnowledgeReportRequest,
    background_tasks: BackgroundTasks,
    service: KnowledgeReportService = Depends(get_knowledge_report_service),
) -> KnowledgeReport:
    report = await service.approve(
        report_id=report_id,
        selected_urls=body.selected_urls,
        resolution=body.resolution,
        reviewer_note=body.reviewer_note,
    )
    background_tasks.add_task(service.run_ingest, report.report_id)
    return report


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
