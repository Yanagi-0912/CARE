from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import (
    CurrentUser,
    get_current_user,
    get_knowledge_report_service,
    get_manual_report_quota,
)
from app.i18n.messages import t
from app.models.knowledge_report import (
    CreateKnowledgeReportRequest,
    CreateKnowledgeReportResponse,
    KnowledgeReportListResponse,
)
from app.services.knowledge_reports.service import KnowledgeReportService
from app.services.rag.whitelist import UrlNotAllowedError, assert_allowed_urls

router = APIRouter()

# 滾動視窗，不是自然日：自然日會讓人在午夜前後送兩倍（design.md 決策 5）
MANUAL_QUOTA_WINDOW = timedelta(hours=24)


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
    quota: int = Depends(get_manual_report_quota),
) -> CreateKnowledgeReportResponse:
    """手動送出的知識回報。

    驗證放在這裡而不是 service：service.create 同時被 create_from_web_fallback
    內部呼叫，把白名單塞進去會讓白名單一收緊就使自動建報靜默失敗
    （design.md 決策 1）。router 是人工輸入進入系統的邊界，驗證就放在邊界上。

    分工：422 給「你少填了東西」（Pydantic，前端表單自己會先擋），
    400 給「你填的網址不能收」，429 給「今天送太多了」——後兩者要向使用者
    解釋清楚，所以走結構化 detail 讓前端對應自己的六語文案。
    """
    # 一次回報全部不合格網址（而非遇到第一個就中止）。detail 形狀與 admin
    # approve 端點一致：同一個概念在同一支 API 不該有兩種形狀。
    try:
        normalized_urls = assert_allowed_urls(body.user_source_urls)
    except UrlNotAllowedError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "url_not_allowed",
                "invalid_urls": [
                    {"url": item.url, "reason": item.reason} for item in exc.invalid
                ],
                "message": t("url.reject.summary").format(count=len(exc.invalid)),
            },
        ) from exc

    since = datetime.now(timezone.utc) - MANUAL_QUOTA_WINDOW
    used = await service.count_manual_reports_since(current_user.line_user_id, since)
    if used >= quota:
        raise HTTPException(
            status_code=429,
            detail={"code": "quota_exceeded", "limit": quota},
        )

    report = await service.create(
        line_user_id=current_user.line_user_id,
        question=body.question,
        reason=body.reason,
        user_note=body.user_note,
        # 存正規化後的網址：admin 看到的、日後 ingest 實際抓的，都是同一份字串
        user_source_urls=normalized_urls,
        source="manual",
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
