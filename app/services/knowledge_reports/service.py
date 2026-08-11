from __future__ import annotations

import logging
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.i18n.messages import t
from app.models.knowledge_report import (
    IngestJob,
    IngestJobResult,
    KnowledgeReport,
    KnowledgeReportReason,
    KnowledgeReportSource,
)
from app.repositories.knowledge_report_repository import KnowledgeReportRepository
from app.services.rag.ingest_service import IngestService
from app.services.rag.whitelist import UrlNotAllowedError, UrlPolicy, default_url_policy

logger = logging.getLogger(__name__)

# 超過此時間仍停在 running 的 job 視為服務重啟遺留的孤兒，允許重新核准取代
INGEST_JOB_STALE_AFTER = timedelta(minutes=10)
# report_id 碰撞的重試上限。位數維持 4 碼——改 6 碼只降低機率不消除問題，
# 且會讓既有的長度斷言變紅；重試才是正解（design.md 決策 10）。
_REPORT_ID_MAX_ATTEMPTS = 5


class KnowledgeReportService:
    def __init__(
        self,
        *,
        repository: KnowledgeReportRepository,
        ingest_service: Optional[IngestService] = None,
        url_policy: UrlPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._ingest_service = ingest_service
        self._url_policy = url_policy or default_url_policy()

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
        source: KnowledgeReportSource | None = None,
    ) -> KnowledgeReport:
        """把一筆回報寫進去。

        這裡刻意 **不** 做白名單驗證與配額檢查：create_from_web_fallback 內部
        就是呼叫本方法，把驗證塞進來會讓白名單一收緊就使自動建報拋例外，而
        web_search_service.py 的 except Exception 會靜默吞掉它（design.md
        決策 1）。判斷「這筆回報從哪來、可不可信」是呼叫端的責任。
        """
        now = datetime.now(timezone.utc)
        # report_id 是 unique index 而亂碼只有 4 碼，碰撞會是 DuplicateKeyError。
        # 換一個編號重試，而不是讓它變成 500（design.md 決策 10）。上限 5 次：
        # 無上限的迴圈在 unique index 因其他欄位衝突時會變成無窮迴圈。
        for attempt in range(_REPORT_ID_MAX_ATTEMPTS):
            report = KnowledgeReport(
                report_id=self._generate_report_id(now),
                line_user_id=line_user_id,
                status="pending",
                reason=reason,
                question=question.strip(),
                user_note=user_note.strip() if user_note else None,
                user_source_urls=list(user_source_urls or []),
                source=source,
                created_at=now,
                updated_at=now,
            )
            try:
                return await self._repository.insert(report)
            except DuplicateKeyError:
                if attempt == _REPORT_ID_MAX_ATTEMPTS - 1:
                    raise
                logger.warning(
                    "report_id 碰撞，重新產生編號重試（第 %s 次）：%s",
                    attempt + 1,
                    report.report_id,
                )
        # 迴圈只會以 return 或 raise 收場，這行僅為型別完整性
        raise AssertionError("unreachable")

    async def count_manual_reports_since(
        self, line_user_id: str, since: datetime
    ) -> int:
        """供 router 做配額檢查；router 不直接碰 repository。"""
        return await self._repository.count_manual_by_line_user_since(
            line_user_id, since
        )

    async def create_from_web_fallback(
        self,
        *,
        question: str,
        urls: list[str],
        line_user_id: str,
    ) -> KnowledgeReport | None:
        normalized_urls = [url.strip() for url in urls if url and url.strip()]
        if not normalized_urls:
            return None

        await self._repository.delete_pending_or_reviewing_by_urls(normalized_urls)
        return await self.create(
            line_user_id=line_user_id,
            question=question,
            reason="missing",
            user_note="auto:web-fallback",
            user_source_urls=normalized_urls,
            source="web_fallback",
        )

    async def list_for_user(self, line_user_id: str) -> list[KnowledgeReport]:
        return await self._repository.list_by_line_user_id(line_user_id)

    async def list_for_admin(
        self,
        status: str | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[KnowledgeReport], int, dict[str, int]]:
        """回傳 (本頁回報, 符合條件總筆數, 待審佇列各狀態筆數)。

        status_counts 恆定回傳 pending／reviewing 的實際筆數，不受本次篩選影響，
        讓前端不必用已載入的頁自行推算——那只反映已載入的部分。
        """
        if status:
            statuses = [status]
        else:
            statuses = ["pending", "reviewing"]
        reports = await self._repository.list_by_statuses(
            statuses, limit=limit, offset=offset
        )
        total = await self._repository.count_by_statuses(statuses)
        status_counts = {
            "pending": await self._repository.count_by_statuses(["pending"]),
            "reviewing": await self._repository.count_by_statuses(["reviewing"]),
        }
        return reports, total, status_counts

    @staticmethod
    def _is_ingest_in_progress(job: IngestJob | None, now: datetime) -> bool:
        """job 是否仍在進行中。status 為 None 的舊紀錄一律視為已結束。"""
        if job is None or job.status != "running":
            return False
        if job.started_at is None:
            return True
        started_at = job.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        return now - started_at < INGEST_JOB_STALE_AFTER

    async def approve(
        self,
        *,
        report_id: str,
        selected_urls: list[str] | None = None,
        resolution: str | None = None,
        reviewer_note: str | None = None,
    ) -> KnowledgeReport:
        """驗證並登記 ingest 工作後立即回傳；實際 ingest 由 run_ingest 於背景執行。"""
        report = await self._repository.find_by_report_id(report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Report not found")

        if report.status in ("resolved", "rejected"):
            raise HTTPException(
                status_code=409,
                detail=f"Report already {report.status}",
            )

        now = datetime.now(timezone.utc)
        if self._is_ingest_in_progress(report.ingest_job, now):
            raise HTTPException(status_code=409, detail="Ingest already running")

        normalized_urls = [
            url.strip() for url in (selected_urls or []) if url and url.strip()
        ]
        if not normalized_urls:
            normalized_urls = [
                url.strip()
                for url in report.user_source_urls
                if url and url.strip()
            ]
        if not normalized_urls:
            raise HTTPException(status_code=400, detail="selected_urls cannot be empty")

        # whitelist.py 不 import fastapi／i18n（design.md Decision 7）：讓它知道
        # HTTP 狀態碼會把信任邊界綁死在一個 transport 上，change 3 的 agent
        # tool 路徑不走 HTTP。把 UrlNotAllowedError 轉成 HTTPException、把
        # 文案接上 i18n，是呼叫端（這裡）的責任。
        try:
            # assert_allowed 回傳的是正規化後的清單：admin 在畫面上看到、
            # ingest job 實際登記的，都是同一份會拿去抓取的字串。
            normalized_urls = self._url_policy.assert_allowed(normalized_urls)
        except UrlNotAllowedError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "url_not_allowed",
                    "invalid_urls": [
                        {"url": item.url, "reason": item.reason}
                        for item in exc.invalid
                    ],
                    # 一次列出全部不合格網址（而非遇到第一個就中止），admin
                    # 貼 5 個錯 3 個時，一次就能改完，不必來回送出三次。
                    "message": t("url.reject.summary").format(count=len(exc.invalid)),
                },
            ) from exc

        if self._ingest_service is None:
            raise HTTPException(status_code=503, detail="Ingest service not configured")

        job = IngestJob(
            selected_urls=normalized_urls,
            results=[],
            status="running",
            started_at=now,
        )
        # 條件式登記：併發的第二個 approve 會在這裡落空而非各排一個背景工作
        acquired = await self._repository.start_ingest_job(
            report_id=report_id,
            job=job,
            stale_before=now - INGEST_JOB_STALE_AFTER,
            resolution=resolution,
            reviewer_note=reviewer_note,
        )
        if not acquired:
            raise HTTPException(status_code=409, detail="Ingest already running")

        report.status = "reviewing"
        # patch 語意：沒帶就沿用原值，重試不會清掉上次寫的備註
        if resolution is not None:
            report.resolution = resolution
        if reviewer_note is not None:
            report.reviewer_note = reviewer_note
        report.ingest_job = job
        report.updated_at = now
        return report

    async def run_ingest(self, report_id: str) -> KnowledgeReport | None:
        """背景執行 approve 登記的 ingest 工作，並將結果寫回報告。"""
        report = await self._repository.find_by_report_id(report_id)
        if report is None or report.ingest_job is None:
            return None

        job = report.ingest_job
        started_at = job.started_at
        results: list[IngestJobResult] = []
        try:
            if self._ingest_service is None:
                raise RuntimeError("Ingest service not configured")

            all_ok = True
            for url in job.selected_urls:
                ingest_result = await self._ingest_service.ingest_url(url)
                results.append(
                    IngestJobResult(
                        url=ingest_result.url,
                        status=ingest_result.status,
                        chunk_count=ingest_result.chunk_count,
                        message=ingest_result.message,
                    )
                )
                if ingest_result.status != "ok":
                    all_ok = False

            job.results = results
            if all_ok:
                report.status = "resolved"
                job.status = "succeeded"
                job.error = None
            else:
                report.status = "reviewing"
                job.status = "failed"
                failed = [r for r in results if r.status != "ok"]
                job.error = "; ".join(
                    f"{r.url}: {r.status} ({r.message or 'failed'})" for r in failed
                )
        except Exception as exc:  # 不讓 job 停在 running
            report.status = "reviewing"
            job.status = "failed"
            # 保留崩潰前已完成的部分，重試時看得出哪些已經成功
            job.results = results
            job.error = f"ingest job crashed: {exc}"

        job.finished_at = datetime.now(timezone.utc)
        report.updated_at = job.finished_at
        # 條件式寫回：期間若被拒絕或被重新 approve 就不命中，本次結果丟棄
        applied = await self._repository.finish_ingest_job(
            report_id=report_id,
            started_at=started_at,
            report_status=report.status,
            job=job,
        )
        return report if applied else None

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
        # 系統無法反收錄，所以不能讓「已拒絕但內容已進向量庫」的狀態成立
        if self._is_ingest_in_progress(report.ingest_job, now):
            raise HTTPException(status_code=409, detail="Ingest already running")

        report.status = "rejected"
        report.reviewer_note = reviewer_note
        report.resolution = resolution
        report.updated_at = now
        return await self._repository.update(report)
