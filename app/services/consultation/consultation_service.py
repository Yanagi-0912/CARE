# 諮詢功能的核心服務，負責處理諮詢訊息的記錄、摘要生成和搜尋等邏輯。
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional
from app.models.consultation import (
    ConsultationMessage,
    ConsultationSummarizeRequest,
    ConsultationSummary,
    ConsultationViewResponse,
)
from app.repositories.consultation_repository import ConsultationRepository
from app.services.consultation.context import (
    ConsultationContext,
    get_current_consultation_context,
)
from app.services.consultation.store import ConsultationStore
from app.services.gemini.services import GeminiService
from app.services.gemini.shared.errors import raise_mapped_gemini_error


@dataclass(frozen=True)
class ConsultationRecordResult:
    stored: bool


class ConsultationService:
    def __init__(
        self,
        *,
        # store是儲存對話訊息的抽象介面，實際上由 RedisConsultationStore 實作
        store: ConsultationStore,
        # repository是儲存諮詢摘要的抽象介面，實際上由 MongoDBConsultationRepository 實作
        repository: ConsultationRepository,
        # 用來生成最後摘要
        gemini_service: GeminiService,
    ) -> None:
        self._store = store
        self._repository = repository
        self._gemini_service = gemini_service

    # record_user_message負責記錄使用者的訊息，從當前的 ConsultationContext
    # 取得必要資訊， 然後將訊息封裝成 ConsultationMessage 並存入 store。
    async def record_user_message(self, user_input: str) -> ConsultationRecordResult:
        import logging

        logger = logging.getLogger(__name__)
        context = get_current_consultation_context()
        logger.info(
            f"[ConsultationService.record_user_message] context={context}, line_id={context.line_id if context else None}"
        )
        if context is None or not context.line_id:
            logger.warning(
                f"[ConsultationService.record_user_message] 失敗：context 或 line_id 為空"
            )
            return ConsultationRecordResult(stored=False)

        raw_text = self._normalize_raw_text(
            context.message_type, context.raw_message, user_input
        )

        message = ConsultationMessage(
            line_id=context.line_id,
            message_type=context.message_type,
            content=user_input,
            raw_text=raw_text,
            timestamp=context.event_time or datetime.now(timezone.utc),
        )
        await self._store.append_message(context.line_id, message)
        return ConsultationRecordResult(stored=True)

    # 負責記錄agent的回覆訊息，邏輯與 record_user_message 類似，但訊息類型固定為
    # "assistant_reply"。
    async def record_assistant_message(
        self, assistant_text: str
    ) -> ConsultationRecordResult:
        context = get_current_consultation_context()
        if context is None or not context.line_id:
            return ConsultationRecordResult(stored=False)

        message = ConsultationMessage(
            line_id=context.line_id,
            message_type="assistant_reply",
            content=assistant_text,
            raw_text=assistant_text,
            timestamp=datetime.now(timezone.utc),
        )
        await self._store.append_message(context.line_id, message)
        return ConsultationRecordResult(stored=True)

    # 這裡的 get_view 方法會優先嘗試從 repository 取得最新的摘要，如果沒有摘要才會
    # 從 store 取得原始訊息列表。

    async def get_view(
        self, user_id: str, target_date: Optional[date] = None
    ) -> ConsultationViewResponse:
        if target_date is None:
            summary = await self._repository.get_latest_summary(user_id)
            if summary is not None:
                return ConsultationViewResponse(
                    line_id=user_id,
                    view_type="summary",
                    summary=summary.summary,
                )

            return ConsultationViewResponse(
                line_id=user_id,
                view_type="summary",
                summary=None,
            )

        summary = await self._repository.get_summary(user_id, target_date)
        if summary is not None:
            return ConsultationViewResponse(
                line_id=user_id,
                view_type="summary",
                summary=summary.summary,
            )

        return ConsultationViewResponse(
            line_id=user_id,
            view_type="summary",
            summary=None,
        )

    # get_raw_view 方法則是直接從 store 取得原始訊息列表，不考慮是否有摘要。
    async def get_raw_view(
        self, user_id: str, target_date: Optional[date] = None
    ) -> ConsultationViewResponse:
        messages = await self._store.list_messages(user_id)
        if target_date is not None:
            messages = [
                message
                for message in messages
                if message.timestamp.date() == target_date
            ]
        return ConsultationViewResponse(
            line_id=user_id,
            view_type="raw",
            messages=messages,
        )

    async def get_all_summaries(self, user_id: str) -> list[ConsultationSummary]:
        return await self._repository.get_all_summaries(user_id)

    # summarize 方法會直接從 store 取得目前使用者的完整對話列表，
    # 然後使用 Gemini 生成摘要，最後將摘要存到 repository。
    async def summarize(
        self, user_id: str, request: ConsultationSummarizeRequest
    ) -> ConsultationSummary:
        messages = await self._store.list_messages(user_id)

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
        messages = await self._store.list_messages(user_id)
        if not messages:
            return None
        return await self.summarize(user_id, ConsultationSummarizeRequest())

    @staticmethod
    def _normalize_raw_text(
        message_type: str, raw_message: Optional[object], fallback: str
    ) -> str:
        """根據訊息類型從原始 message 物件提取並格式化 raw_text。

        Args:
            message_type: LINE 訊息類型 (text, location, image, video, audio, file 等)
            raw_message: LINE message 物件
            fallback: 若無法提取時的預設值（通常是 user_input 或 assistant_text）

        Returns:
            格式化後的 raw_text 字串
        """
        if raw_message is None:
            return fallback

        if message_type == "text":
            return getattr(raw_message, "text", fallback)
        elif message_type == "location":
            lat = getattr(raw_message, "latitude", "")
            lng = getattr(raw_message, "longitude", "")
            return f"lat={lat}, lng={lng}" if lat or lng else fallback
        elif message_type == "file":
            file_name = getattr(raw_message, "file_name", None)
            media_id = getattr(raw_message, "id", None)
            return file_name or media_id or fallback
        elif message_type in {"image", "video", "audio"}:
            media_id = getattr(raw_message, "id", None)
            return media_id or fallback
        else:
            return fallback

    @staticmethod
    def _normalize_timestamp(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)

    # 真正呼叫gemini做摘要
    async def _generate_summary(
        self, user_id: str, target_date: date, messages: list[ConsultationMessage]
    ) -> str:
        if not messages:
            return "該日期尚無諮詢記錄。"

        transcript_lines = []
        for message in messages:
            transcript_lines.append(f"[{message.message_type}] {message.content}")
            print(f"message: {message}")
        prompt = f"""
        你是醫療諮詢摘要助手。
        請根據對話輸出 JSON。

        規則：
        - 僅輸出 JSON
        - 不要 markdown
        - 不要額外說明
        - 不要輸出任何空陣列 []
        - 只要沒有資料，就直接填寫「無」
        - 若某欄位有多個項目，請用「、」分隔成單一字串

        schema:
        {{
        "主訴": string,
        "症狀": string,
        "檢查": string,
        "建議": string,
        "重要時間點": string,
        "其他": string,
        "AI小摘要": string
        }}

        輸出格式注意：
        - 每個欄位都必須是可直接閱讀的中文字串
        - 若該欄位沒有可填內容，請寫「無」
        - "AI小摘要" 請用 1 到 3 句話總結整體重點，並給出下一步建議或提醒

        日期：{target_date.isoformat()}

        對話：
        {transcript_lines}
        """

        try:
            result = await self._gemini_service._chat_llm.ainvoke(prompt)
        except Exception as exc:
            raise_mapped_gemini_error(exc)
        content = getattr(result, "content", "")
        summary_text = str(content).strip()
        return summary_text or "該日期尚無可摘要內容。"
