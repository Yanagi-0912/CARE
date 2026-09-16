# 諮詢功能的核心服務，負責處理諮詢訊息的摘要生成和搜尋等邏輯。
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from textwrap import dedent
from typing import Optional
from app.models.chat_message import ChatMessage
from app.models.consultation import (
    ConsultationSummarizeRequest,
    ConsultationSummary,
)
from app.models.medication import TAIPEI_TZ, ensure_aware_utc
from app.repositories.consultation_repository import ConsultationRepository
from app.repositories.conversation_log_repository import ConversationLogRepository
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


def _taipei_date(timestamp: datetime) -> date:
    # 訊息時間戳存的是 UTC；「哪一天的對話」要以使用者所在的台北日期為準，
    # 否則台北 00:00–08:00 的對話會被算到前一天。
    return ensure_aware_utc(timestamp).astimezone(TAIPEI_TZ).date()


def taipei_day_utc_range(day: date) -> tuple[datetime, datetime]:
    """某個台北日期對應的 UTC 半開區間 `[start, end)`。

    給資料庫查詢用：訊息以 UTC 存，「台北 9/14」是 UTC 9/13 16:00 到 9/14 16:00。
    """
    start = datetime.combine(day, time.min, tzinfo=TAIPEI_TZ).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


class ConsultationService:
    def __init__(
        self,
        *,
        # 對話原文的正式紀錄（Mongo，保存 30 天）。摘要與原始訊息查詢都讀這份，
        # 不讀 Redis——那只是 agent 用的快取，只留最近幾則、一天就過期。
        chat_history_repository: ConversationLogRepository,
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
        if not messages:
            return []
        if target_date is None:
            # LIFF 的原始紀錄頁把訊息攤成一整串、沒有日期標示；原文存 30 天，
            # 全部回傳會變成分不清哪天的長串，所以只給最近有對話的那一天。
            target_date = max(_taipei_date(message.timestamp) for message in messages)
        return [
            message
            for message in messages
            if _taipei_date(message.timestamp) == target_date
        ]

    async def get_all_summaries(self, user_id: str) -> list[ConsultationSummary]:
        return await self._repository.get_all_summaries(user_id)

    # 摘要使用者最近一次對話所在的那一天（台北日期）。
    async def summarize(
        self, user_id: str, request: ConsultationSummarizeRequest
    ) -> ConsultationSummary:
        messages = await self._chat_history_repository.list_messages(user_id)
        if messages:
            latest_message = max(
                messages, key=lambda message: ensure_aware_utc(message.timestamp)
            )
            target_date = _taipei_date(latest_message.timestamp)
        else:
            target_date = datetime.now(TAIPEI_TZ).date()
        return await self._summarize_date(
            user_id, target_date, messages, force=request.force
        )

    # 每日排程用：只摘指定的那一個台北日期（排程傳昨天）。
    #
    # 以前是把每個人 30 天的原文全撈回來、每個有訊息的日期都補一次——資料量是
    # O(使用者數 × 30 天)，而其中 29 天的摘要早就存在，撈回來只是為了確認一遍。
    # 現在查詢就限定在那一天的 UTC 區間，只帶回需要的訊息；那天沒講話的人回
    # None，不寫「尚無諮詢記錄」那種空摘要。手動摘要（`summarize`）不走這裡。
    async def summarize_day(
        self, user_id: str, target_date: date
    ) -> Optional[ConsultationSummary]:
        since, until = taipei_day_utc_range(target_date)
        messages = await self._chat_history_repository.list_messages(
            user_id, since=since, until=until
        )
        if not messages:
            return None
        return await self._summarize_date(user_id, target_date, messages, force=False)

    async def _summarize_date(
        self,
        user_id: str,
        target_date: date,
        messages: list[ChatMessage],
        *,
        force: bool,
    ) -> ConsultationSummary:
        # Redis 列表的 TTL 每寫一則就重設，天天聊天的人會跨好幾天，只取當天的
        day_messages = [
            message
            for message in messages
            if _taipei_date(message.timestamp) == target_date
        ]

        existing = await self._repository.get_summary_by_date(user_id, target_date)
        if existing is not None and not force:
            # 摘要產生後當天又有新對話就重做，否則後面的對話永遠不會被摘要
            summarized_at = ensure_aware_utc(existing.created_at)
            if all(
                ensure_aware_utc(message.timestamp) <= summarized_at
                for message in day_messages
            ):
                return existing

        language = await self._resolve_summary_language(user_id)
        summary_text = await self._generate_summary(
            user_id,
            target_date,
            day_messages,
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
