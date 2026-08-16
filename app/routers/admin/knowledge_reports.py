from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.dependencies import (
    get_content_preview_service,
    get_knowledge_report_service,
    require_admin_user,
)
from app.models.knowledge_report import (
    ApproveKnowledgeReportRequest,
    ContentPreview,
    KnowledgeReport,
    KnowledgeReportListResponse,
    KnowledgeReportStatus,
    RejectKnowledgeReportRequest,
    StartContentPreviewRequest,
)
from app.services.knowledge_reports.preview_service import ContentPreviewService
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
        "核准的對象是一份具體的內容快照：請帶上 preview_id 與各選定 URL 的 "
        "content_hashes，預覽逾期、已被取代或雜湊不符時回 409。"
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
        preview_id=body.preview_id,
        content_hashes=body.content_hashes,
    )
    background_tasks.add_task(service.run_ingest, report.report_id)
    return report


@router.post(
    "/{report_id}/preview",
    response_model=ContentPreview,
    status_code=202,
    summary="啟動核准前的內容預覽",
    description=(
        "同步驗證回報存在、URL 通過白名單且數量未超上限後立即回 202，"
        "實際抓取於回應送出後在背景執行。TTL 內已有相同 URL 集合的就緒預覽時"
        "直接沿用而不重抓，force=true 可強制重新抓取並取得新的 preview_id。"
    ),
)
async def start_content_preview(
    report_id: str,
    body: StartContentPreviewRequest,
    background_tasks: BackgroundTasks,
    service: KnowledgeReportService = Depends(get_knowledge_report_service),
    preview_service: ContentPreviewService = Depends(get_content_preview_service),
) -> ContentPreview:
    # 先確認回報存在，再讓預覽服務做 URL 驗證：對不存在的回報去打外部網站
    # 是沒有意義的抓取。
    report = await service.get_for_review(report_id)
    urls = body.urls or report.user_source_urls
    started = await preview_service.start(
        report_id=report_id, urls=urls, force=body.force
    )
    if started.scheduled:
        background_tasks.add_task(
            preview_service.run,
            report_id=report_id,
            preview_id=started.preview.preview_id,
            urls=started.urls,
        )
    return started.preview


@router.get(
    "/{report_id}/preview",
    response_model=ContentPreview,
    summary="取得核准前的內容預覽",
    description="回傳逐 URL 的抓取狀態、標題、字數、內容雜湊與（截斷後的）原文；無預覽或已逾期回 404。",
)
async def get_content_preview(
    report_id: str,
    preview_service: ContentPreviewService = Depends(get_content_preview_service),
) -> ContentPreview:
    preview = await preview_service.get(report_id)
    if preview is None:
        raise HTTPException(status_code=404, detail="Preview not found")
    return preview


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
