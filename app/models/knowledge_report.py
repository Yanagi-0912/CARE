from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

KnowledgeReportStatus = Literal["pending", "reviewing", "resolved", "rejected"]
KnowledgeReportReason = Literal["outdated", "missing", "other"]
IngestJobStatus = Literal["running", "succeeded", "failed"]


class IngestJobResult(BaseModel):
    url: str
    status: str
    chunk_count: int = 0
    message: str = ""


class IngestJob(BaseModel):
    selected_urls: list[str] = Field(default_factory=list)
    results: list[IngestJobResult] = Field(default_factory=list)
    error: Optional[str] = None
    # None 代表本欄位加入前寫下的舊紀錄，一律視為已結束
    status: Optional[IngestJobStatus] = Field(default=None, description="ingest 執行狀態")
    started_at: Optional[datetime] = Field(default=None, description="ingest 開始時間")
    finished_at: Optional[datetime] = Field(default=None, description="ingest 結束時間")


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
    # 以下分頁欄位僅 admin 待審列表會填；使用者端個人列表維持 None
    total: Optional[int] = Field(default=None, description="符合篩選條件的總筆數")
    limit: Optional[int] = Field(default=None, description="本次查詢的每頁筆數")
    offset: Optional[int] = Field(default=None, description="本次查詢的位移")
    status_counts: Optional[dict[str, int]] = Field(
        default=None,
        description="待審佇列各狀態的實際筆數，不受 status 篩選與分頁影響",
    )


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
