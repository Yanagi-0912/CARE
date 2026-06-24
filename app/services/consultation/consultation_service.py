# 諮詢功能的核心服務，負責處理諮詢訊息的摘要生成和搜尋等邏輯。
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional
from app.models.chat_message import ChatMessage
from app.models.consultation import (
    ConsultationSummarizeRequest,
    ConsultationSummary,
)
from app.repositories.consultation_repository import ConsultationRepository
from app.repositories.chat_history_repository import ChatHistoryRepository
from app.services.gemini.services import GeminiService


class ConsultationService:
    def __init__(
        self,
        *,
        # chat_history_repository 是儲存對話訊息的抽象介面，實際上由 RedisChatHistoryRepository 實作
        chat_history_repository: ChatHistoryRepository,
        # repository 是儲存諮詢摘要的抽象介面，實際上由 MongoDBConsultationRepository 實作
        repository: ConsultationRepository,
        # 用來生成最後摘要
        gemini_service: GeminiService,
    ) -> None:
        self._chat_history_repository = chat_history_repository
        self._repository = repository
        self._gemini_service = gemini_service

    # 這裡的 get_view 方法會優先嘗試從 repository 取得特定的摘要，如果沒有傳入日期則取得最新摘要。
    async def get_view(
        self, user_id: str, target_date: Optional[date] = None
    ) -> Optional[ConsultationSummary]:
        if target_date is None:
            return await self._repository.get_latest_summary(user_id)
        return await self._repository.get_summary(user_id, target_date)

    # get_raw_view 方法則是直接從 chat_history_repository 取得原始訊息列表，不考慮是否有摘要。
    async def get_raw_view(
        self, user_id: str, target_date: Optional[date] = None
    ) -> list[ChatMessage]:
        messages = await self._chat_history_repository.list_messages(user_id)
        if target_date is not None:
            messages = [
                message
                for message in messages
                if message.timestamp.date() == target_date
            ]
        return messages

    async def get_all_summaries(self, user_id: str) -> list[ConsultationSummary]:
        return await self._repository.get_all_summaries(user_id)

    # summarize 方法會直接從 chat_history_repository 取得目前使用者的完整對話列表，
    # 然後使用 Gemini 生成摘要，最後將摘要存到 repository。
    async def summarize(
        self, user_id: str, request: ConsultationSummarizeRequest
    ) -> ConsultationSummary:
        messages = await self._chat_history_repository.list_messages(user_id)

        if messages:
            latest_message = max(
                messages,
                key=lambda message: self._normalize_timestamp(message.timestamp),
            )
            target_date = latest_message.timestamp.date()
        else:
            target_date = date.today()

        existing = await self._repository.get_summary(user_id, target_date)
        if existing is not None and not request.force:
            return existing

        # 調用內部方法 _generate_summary 來產生摘要
        summary_text = await self._generate_summary(user_id, target_date, messages)
        summary = ConsultationSummary(
            line_id=user_id,
            summary_date=target_date,
            summary=summary_text,
            created_at=datetime.now(timezone.utc),
        )
        await self._repository.upsert_summary(summary)
        return summary

    # 用來檢查今天是否已經有摘要，如果沒有就自動生成。
    async def summarize_today_if_needed(
        self, user_id: str
    ) -> Optional[ConsultationSummary]:
        messages = await self._chat_history_repository.list_messages(user_id)
        if not messages:
            return None
        return await self.summarize(user_id, ConsultationSummarizeRequest())

    @staticmethod
    def _normalize_timestamp(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)

    # 真正呼叫 gemini 做摘要
    async def _generate_summary(
        self, user_id: str, target_date: date, messages: list[ChatMessage]
    ) -> str:
        return await self._gemini_service.generate_consultation_summary(target_date, messages)
