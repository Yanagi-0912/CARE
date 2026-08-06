# 諮詢功能的核心服務，負責處理諮詢訊息的摘要生成和搜尋等邏輯。
from __future__ import annotations

from datetime import date, datetime, timezone
from textwrap import dedent
from typing import Optional
from app.models.chat_message import ChatMessage
from app.models.consultation import (
    ConsultationSummarizeRequest,
    ConsultationSummary,
)
from app.repositories.consultation_repository import ConsultationRepository
from app.repositories.chat_history_repository import ChatHistoryRepository
from app.services.gemini.services import GeminiService
from app.services.gemini.shared.errors import raise_mapped_gemini_error
from app.services.users.user_profile_service import UserProfileService
from langchain_core.messages import HumanMessage

DEFAULT_SUMMARY_LANGUAGE = "zh-TW"
SUPPORTED_SUMMARY_LANGUAGES = {
    "zh-TW": "繁體中文",
    "en": "英文",
    "id": "印尼文",
    "vi": "越南文",
    "th": "泰文",
    "ja": "日文",
}


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
        # 讀取使用者偏好的語言
        user_profile_service: UserProfileService,
    ) -> None:
        self._chat_history_repository = chat_history_repository
        self._repository = repository
        self._gemini_service = gemini_service
        self._user_profile_service = user_profile_service

    # 這裡的 get_view 方法會優先嘗試從 repository 取得特定的摘要，如果沒有傳入日期則取得最新摘要。
    async def get_view(
        self, user_id: str, target_date: Optional[date] = None
    ) -> Optional[ConsultationSummary]:
        if target_date is None:
            return await self._repository.get_latest_summary(user_id)
        return await self._repository.get_summary_by_date(user_id, target_date)

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
        language = await self._resolve_summary_language(user_id)

        if messages:
            latest_message = max(
                messages,
                key=lambda message: self._normalize_timestamp(message.timestamp),
            )
            target_date = latest_message.timestamp.date()
        else:
            target_date = date.today()

        existing = await self._repository.get_summary_by_date(user_id, target_date)
        if existing is not None and not request.force:
            return existing

        # 調用內部方法 _generate_summary 來產生摘要
        summary_text = await self._generate_summary(
            user_id,
            target_date,
            messages,
            language=language,
        )
        summary = ConsultationSummary(
            line_id=user_id,
            summary_date=target_date,
            summary=summary_text,
            language=language,
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

    async def _resolve_summary_language(self, user_id: str) -> str:
        settings = await self._user_profile_service.get_user_settings(user_id)
        language = (settings or {}).get("language")
        if language not in SUPPORTED_SUMMARY_LANGUAGES:
            return DEFAULT_SUMMARY_LANGUAGE
        return language

    @staticmethod
    def _language_name(language: str) -> str:
        return SUPPORTED_SUMMARY_LANGUAGES.get(
            language, SUPPORTED_SUMMARY_LANGUAGES[DEFAULT_SUMMARY_LANGUAGE]
        )

    # 真正呼叫 gemini 做摘要
    async def _generate_summary(
        self,
        user_id: str,
        target_date: date,
        messages: list[ChatMessage],
        *,
        language: str,
    ) -> str:
        if not messages:
            return "該日期尚無諮詢記錄。"

        language_name = self._language_name(language)
        transcript_lines = []
        for message in messages:
            transcript_lines.append(f"[{message.message_type}] {message.content}")
        prompt = dedent(f"""
            你是醫療諮詢摘要助手。
            請根據對話輸出 JSON。
            使用者資料庫語言：{language}（{language_name}）
            請以該語言撰寫各欄位內容。
            JSON 欄位 key 請依照{language_name}翻譯成對應語言。

            規則：
            - 僅輸出 JSON
            - "症狀"欄位只能填寫使用者自己明確提到的症狀，不可包含 AI 回覆中提到的內容
            - 「建議」只填寫 AI 給出的核心行動建議，大約 3-5 項
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
            - 每個欄位都必須是可直接閱讀的{language_name}字串
            - 若該欄位沒有可填內容，請寫「無」
            - "AI小摘要" 請用 1 到 3 句話總結整體重點，並給出下一步建議或提醒

            日期：{target_date.isoformat()}

            對話：
            {transcript_lines}
            """).strip()

        try:
            result = await self._gemini_service.chat_model.ainvoke(
                [HumanMessage(content=prompt)]
            )
        except Exception as exc:
            raise_mapped_gemini_error(exc)
        content = getattr(result, "content", "")
        summary_text = str(content).strip()
        return summary_text or "該日期尚無可摘要內容。"
