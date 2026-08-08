from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

KnowledgeReportStatus = Literal["pending", "reviewing", "resolved", "rejected"]
KnowledgeReportReason = Literal["outdated", "missing", "other"]


class IngestJobResult(BaseModel):
    url: str
    status: str
    chunk_count: int = 0
    message: str = ""


class IngestJob(BaseModel):
    selected_urls: list[str] = Field(default_factory=list)
    results: list[IngestJobResult] = Field(default_factory=list)
    error: Optional[str] = None


class KnowledgeReport(BaseModel):
    report_id: str = Field(..., description="唯一回報編號 KR-YYYYMMDD-XXXX")
    line_user_id: str = Field(..., description="LINE user ID")
    status: KnowledgeReportStatus = Field(..., description="回報狀態")
    reason: KnowledgeReportReason = Field(..., description="回報原因")
    question: str = Field(..., description="使用者問題或知識缺口描述")
    user_note: Optional[str] = Field(default=None, description="使用者補充說明")
    user_source_urls: list[str] = Field(
        default_factory=list, description="使用者提供的來源 URL"
    )
    resolution: Optional[str] = Field(default=None, description="審核結論或處置說明")
    reviewer_note: Optional[str] = Field(default=None, description="審核者備註")
    ingest_job: Optional[IngestJob] = Field(default=None, description="核准 ingest 紀錄")
    created_at: datetime = Field(..., description="建立時間")
    updated_at: datetime = Field(..., description="最後更新時間")


class CreateKnowledgeReportRequest(BaseModel):
    question: str = Field(..., min_length=1, description="問題或知識缺口描述")
    reason: KnowledgeReportReason = Field(..., description="回報原因")
    user_note: Optional[str] = Field(default=None, description="使用者補充說明")
    user_source_urls: Optional[list[str]] = Field(
        default=None, description="使用者提供的來源 URL"
    )


class CreateKnowledgeReportResponse(BaseModel):
    report_id: str


class KnowledgeReportListResponse(BaseModel):
    reports: list[KnowledgeReport]


class ApproveKnowledgeReportRequest(BaseModel):
    selected_urls: list[str] = Field(
        default_factory=list,
        description="核准後要 ingest 的 URL；省略或空則使用報告的 user_source_urls",
    )
    resolution: Optional[str] = Field(default=None, description="審核結論")
    reviewer_note: Optional[str] = Field(default=None, description="審核者備註")


class RejectKnowledgeReportRequest(BaseModel):
    reviewer_note: Optional[str] = Field(default=None, description="審核者備註")
    resolution: Optional[str] = Field(default=None, description="拒絕原因或說明")
