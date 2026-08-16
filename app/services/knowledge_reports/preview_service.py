from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException

from app.i18n.messages import t
from app.models.knowledge_report import ContentPreview, ContentPreviewItem
from app.repositories.knowledge_report_preview_repository import (
    KnowledgeReportPreviewRepository,
)
from app.services.rag.whitelist import UrlNotAllowedError, UrlPolicy, default_url_policy

logger = logging.getLogger(__name__)

# 單一頁面原文的安全上限。Mongo 單文件硬上限是 16MB，而一份預覽可能含多個
# URL，所以逐頁抓在遠低於它的位置；超過的頁面記為 error 而不是讓整份快照
# 寫入失敗（design.md 決策 3）。
MAX_CONTENT_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class PreviewStart:
    """`start()` 的結果。

    `scheduled` 為 False 代表沿用了 TTL 內既有的就緒預覽，呼叫端 SHALL NOT
    再排背景抓取——「期限內不重複抓取」這條規則就是靠它成立的。
    """

    preview: ContentPreview
    urls: list[str]
    scheduled: bool


class ContentPreviewService:
    def __init__(
        self,
        *,
        repository: KnowledgeReportPreviewRepository,
        web_client: Any,
        ttl_minutes: int,
        max_urls: int,
        return_max_chars: int,
        url_policy: UrlPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._web_client = web_client
        self._ttl_minutes = ttl_minutes
        self._max_urls = max_urls
        self._return_max_chars = return_max_chars
        self._url_policy = url_policy or default_url_policy()

    @staticmethod
    def _generate_preview_id() -> str:
        return f"PV-{uuid.uuid4().hex[:16]}"

    async def start(
        self,
        *,
        report_id: str,
        urls: list[str],
        force: bool = False,
    ) -> PreviewStart:
        """同步驗證並登記一份 running 的預覽；抓取由呼叫端於回應後執行。"""
        cleaned = [url.strip() for url in (urls or []) if url and url.strip()]
        if not cleaned:
            raise HTTPException(status_code=400, detail="urls cannot be empty")

        # 預覽端點是 URL 進入系統的第一道關卡：assert_allowed 同時完成正規化與
        # 白名單檢查，之後所有環節（快照的鍵、ingest_job.selected_urls、向量庫
        # 的 {"url": url}）一律使用它回傳的這份字串（design.md 決策 5）。
        try:
            normalized = self._url_policy.assert_allowed(cleaned)
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

        if len(normalized) > self._max_urls:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "preview_too_many_urls",
                    "max_urls": self._max_urls,
                    "message": t("preview.reject.too_many_urls").format(
                        max=self._max_urls
                    ),
                },
            )

        now = datetime.now(timezone.utc)
        if not force:
            existing = await self._repository.find_ready(report_id, now=now)
            if existing is not None and {
                item.url for item in existing.items
            } == set(normalized):
                # URL 集合相同且未逾期：直接沿用，不對外部服務發新的抓取請求。
                # 沒有這條，admin 每點開一次待審回報就會燒掉一次 Firecrawl 額度。
                return PreviewStart(
                    preview=self._for_response(existing),
                    urls=normalized,
                    scheduled=False,
                )

        preview = ContentPreview(
            preview_id=self._generate_preview_id(),
            report_id=report_id,
            status="running",
            urls=normalized,
            items=[],
            created_at=now,
            expires_at=now + timedelta(minutes=self._ttl_minutes),
        )
        await self._repository.upsert_for_report(preview)
        return PreviewStart(preview=preview, urls=normalized, scheduled=True)

    async def run(
        self, *, report_id: str, preview_id: str, urls: list[str]
    ) -> bool:
        """背景逐 URL 抓取並寫回快照。回傳結果是否被套用。"""
        items: list[ContentPreviewItem] = []
        try:
            for url in urls:
                items.append(await self._scrape_one(url))
        except Exception as exc:  # 不讓預覽停在 running
            logger.exception("內容預覽抓取崩潰 report_id=%s", report_id)
            items.append(
                ContentPreviewItem(
                    url="",
                    status="error",
                    message=f"preview job crashed: {exc}",
                )
            )

        # 只有每個 URL 都成功才算就緒：任一項失敗都不該被核准，讓整份收斂為
        # failed 也使 find_ready 不會沿用它，下次開啟詳情就會重抓。
        status = "ready" if items and all(i.status == "ok" for i in items) else "failed"
        now = datetime.now(timezone.utc)
        finished = ContentPreview(
            preview_id=preview_id,
            report_id=report_id,
            status=status,
            urls=list(urls),
            items=items,
            created_at=now,
            expires_at=now + timedelta(minutes=self._ttl_minutes),
        )
        return await self._repository.finish(finished)

    async def _scrape_one(self, url: str) -> ContentPreviewItem:
        try:
            page = await self._web_client.scrape_page(url)
        except Exception as exc:
            return ContentPreviewItem(url=url, status="error", message=str(exc))

        text = page.text or ""
        title = (page.title or "").strip()
        if len(text.encode()) > MAX_CONTENT_BYTES:
            return ContentPreviewItem(
                url=url,
                status="error",
                title=title,
                char_count=len(text),
                message=(
                    f"頁面內容超過 {MAX_CONTENT_BYTES // (1024 * 1024)}MB 上限，"
                    "不納入預覽亦無法核准"
                ),
            )

        if not text.strip():
            return ContentPreviewItem(
                url=url,
                status="empty",
                title=title,
                message="抓取結果為空內容",
            )

        return ContentPreviewItem(
            url=url,
            status="ok",
            title=title,
            content=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            char_count=len(text),
        )

    async def get_snapshot(self, report_id: str) -> Optional[ContentPreview]:
        """伺服器端保留的原始快照：內容不截斷、逾期也照樣回傳。

        核准綁定與背景收錄用這一支。前者要能分辨「沒有預覽」與「預覽逾期」
        才給得出正確的 409 訊息，後者要的是完整的原文而不是畫面上那份截斷版。
        對外的 `get()` 才是給呼叫端看的版本。
        """
        return await self._repository.find_by_report_id(report_id)

    async def get(self, report_id: str) -> Optional[ContentPreview]:
        """取回預覽；內容依上限截斷並標記，已逾期視同不存在。"""
        preview = await self._repository.find_by_report_id(report_id)
        if preview is None:
            return None

        expires_at = preview.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            # TTL monitor 每 60 秒才跑一次，逾期文件在被真正刪掉前仍查得到；
            # 逾期即不可再作為核准依據，這裡就當它不存在。
            return None

        return self._for_response(preview)

    def _for_response(self, preview: ContentPreview) -> ContentPreview:
        """回傳給呼叫端的版本：內容依上限截斷，伺服器端那份全文不受影響。"""
        return preview.model_copy(
            update={"items": [self._truncated(item) for item in preview.items]}
        )

    def _truncated(self, item: ContentPreviewItem) -> ContentPreviewItem:
        if len(item.content) <= self._return_max_chars:
            return item
        # char_count 維持截斷前的真實長度、content_hash 維持全文的雜湊：
        # 核准綁定的是伺服器端保留的那份全文，畫面上顯示多少不改變綁定對象。
        return item.model_copy(
            update={
                "content": item.content[: self._return_max_chars],
                "truncated": True,
            }
        )
