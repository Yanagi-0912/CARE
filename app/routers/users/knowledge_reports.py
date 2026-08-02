from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_knowledge_report_service,
)
from app.models.knowledge_report import (
    CreateKnowledgeReportRequest,
    CreateKnowledgeReportResponse,
    KnowledgeReportListResponse,
)
from app.services.knowledge_reports.service import KnowledgeReportService

router = APIRouter()


@router.post(
    "",
    response_model=CreateKnowledgeReportResponse,
    summary="建立知識回報",
    description="已登入使用者提交知識缺口或錯誤回報。",
)
async def create_knowledge_report(
    body: CreateKnowledgeReportRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: KnowledgeReportService = Depends(get_knowledge_report_service),
) -> CreateKnowledgeReportResponse:
    report = await service.create(
        line_user_id=current_user.line_user_id,
        question=body.question,
        reason=body.reason,
        user_note=body.user_note,
        user_source_urls=body.user_source_urls,
    )
    return CreateKnowledgeReportResponse(report_id=report.report_id)


@router.get(
    "",
    response_model=KnowledgeReportListResponse,
    summary="列出知識回報",
    description="取得目前登入使用者的知識回報列表，依建立時間新到舊排序。",
)
async def list_knowledge_reports(
    current_user: CurrentUser = Depends(get_current_user),
    service: KnowledgeReportService = Depends(get_knowledge_report_service),
) -> KnowledgeReportListResponse:
    reports = await service.list_for_user(current_user.line_user_id)
    return KnowledgeReportListResponse(reports=reports)
