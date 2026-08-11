from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.core.config import settings

KnowledgeReportStatus = Literal["pending", "reviewing", "resolved", "rejected"]
KnowledgeReportReason = Literal["outdated", "missing", "other"]
IngestJobStatus = Literal["running", "succeeded", "failed"]
KnowledgeReportSource = Literal["manual", "agent_tool", "web_fallback"]

# 單一 URL 的長度上限。2048 是各家瀏覽器與代理伺服器實務上的共同下限，
# 超過這個長度的來源網址不是正常的衛教頁面連結。
MAX_SOURCE_URL_LENGTH = 2048
# question／user_note 的長度上限。表單是給長輩用的兩欄輸入，不是文章編輯器。
MAX_TEXT_LENGTH = 500


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
    # None 代表本欄位加入前寫下的舊紀錄，一律視為非手動、不佔配額——寧可放行
    # 也不要誤擋既有使用者。用途僅限（1）配額計數（2）admin 顯示來源可信度，
    # SHALL NOT 影響審核、ingest 或去重行為（design.md 決策 4）。
    source: Optional[KnowledgeReportSource] = Field(
        default=None, description="回報建立來源"
    )
    created_at: datetime = Field(..., description="建立時間")
    updated_at: datetime = Field(..., description="最後更新時間")


class CreateKnowledgeReportRequest(BaseModel):
    """使用者手動送出的回報。

    URL 與說明皆為必填：admin 的判斷依據就只有這兩欄，缺一就無法決定該不該
    收（design.md Context）。收緊只作用於這個人工入口——agent tool 與 web
    fallback 不經過本模型，行為完全不變。
    """

    question: str = Field(..., min_length=1, max_length=MAX_TEXT_LENGTH)
    reason: KnowledgeReportReason = Field(..., description="回報原因")
    user_note: str = Field(..., min_length=1, max_length=MAX_TEXT_LENGTH)
    user_source_urls: list[str] = Field(
        ...,
        min_length=1,
        max_length=settings.KNOWLEDGE_REPORT_MAX_SOURCE_URLS,
        description="使用者提供的來源 URL",
    )

    @field_validator("question", "user_note")
    @classmethod
    def _reject_blank_text(cls, value: str) -> str:
        # min_length=1 只看字元數，"   " 會通過。先 strip 再判，並回傳 strip
        # 後的值，避免整串空白繞過長度下限後又被存進資料庫。
        stripped = value.strip()
        if not stripped:
            raise ValueError("不可為空白")
        return stripped

    @field_validator("user_source_urls")
    @classmethod
    def _reject_blank_or_overlong_urls(cls, value: list[str]) -> list[str]:
        # 這裡只做「明顯不是一個可用字串」的檢查。白名單與正規化一律由
        # router 呼叫 assert_allowed_urls 處理，不在模型層重做一份
        # （design.md 決策 1：驗證放在人工輸入進入系統的那個邊界）。
        cleaned: list[str] = []
        for url in value:
            stripped = url.strip()
            if not stripped:
                raise ValueError("網址不可為空白")
            if len(stripped) > MAX_SOURCE_URL_LENGTH:
                raise ValueError(f"網址長度不可超過 {MAX_SOURCE_URL_LENGTH} 字元")
            cleaned.append(stripped)
        return cleaned


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
