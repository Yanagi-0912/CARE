from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.consultation import (
    ConsultationSummary,
    ConsultationSummarizeRequest,
)
from app.models.chat_message import ChatMessage
from app.services.consultation.consultation_service import ConsultationService


class FakeChatHistoryRepository:
    def __init__(self) -> None:
        self.messages: dict[str, list[ChatMessage]] = {}

    async def append_message(self, line_id: str, message: ChatMessage) -> None:
        self.messages.setdefault(line_id, []).append(message)

    async def list_messages(self, line_id: str) -> list[ChatMessage]:
        return list(self.messages.get(line_id, []))


class FakeRepository:
    def __init__(self) -> None:
        self.summary: ConsultationSummary | None = None
        self.summaries: list[ConsultationSummary] = []

    async def get_summary_by_date(self, line_id: str, target_date: date):
        if (
            self.summary
            and self.summary.line_id == line_id
            and self.summary.summary_date == target_date
        ):
            return self.summary
        return None

    async def get_latest_summary(self, line_id: str):
        return (
            self.summary if self.summary and self.summary.line_id == line_id else None
        )

    async def upsert_summary(self, summary: ConsultationSummary):
        self.summary = summary
        return summary

    async def get_all_summaries(self, line_id: str):
        return [summary for summary in self.summaries if summary.line_id == line_id]


class FakeUserProfileService:
    def __init__(self, language: str | None = "zh-TW") -> None:
        self.language = language

    async def get_user_settings(self, line_id: str) -> dict:
        return {"language": self.language}


@pytest.fixture
def consultation_service() -> ConsultationService:
    fake_store = FakeChatHistoryRepository()
    fake_repo = FakeRepository()
    fake_user_profile_service = FakeUserProfileService()
    fake_gemini = SimpleNamespace(chat_model=SimpleNamespace(ainvoke=AsyncMock()))
    return ConsultationService(
        chat_history_repository=fake_store,
        repository=fake_repo,
        gemini_service=fake_gemini,
        user_profile_service=fake_user_profile_service,
    )


# 測試當資料庫有該日期的諮詢摘要時，get_view 可以正確回傳該摘要內容。
@pytest.mark.asyncio
async def test_get_view_prefers_summary(
    consultation_service: ConsultationService,
):
    summary = ConsultationSummary(
        line_id="U123",
        summary_date=date(2026, 5, 17),
        summary="今天主要是腸胃不適",
        language="zh-TW",
        created_at=datetime.now(timezone.utc),
    )
    consultation_service._repository.summary = summary

    summary_res = await consultation_service.get_view("U123", date(2026, 5, 17))

    assert summary_res is not None
    assert summary_res.summary == "今天主要是腸胃不適"


# 測試當資料庫沒有該日期的諮詢摘要時，get_view 會回傳 None，而不會 fallback 到原始訊息。
@pytest.mark.asyncio
async def test_get_view_without_summary_does_not_fallback_to_raw(
    consultation_service: ConsultationService,
):
    summary_res = await consultation_service.get_view("U123")

    assert summary_res is None


# 測試能正確過濾出指定 line_id 的所有歷史摘要，而不會混入其他人的資料。
@pytest.mark.asyncio
async def test_list_summary_history_returns_repository_data(
    consultation_service: ConsultationService,
):
    consultation_service._repository.summaries = [
        ConsultationSummary(
            line_id="U123",
            summary_date=date(2026, 5, 26),
            summary="5/26 摘要",
            language="zh-TW",
            created_at=datetime.now(timezone.utc),
        ),
        ConsultationSummary(
            line_id="U999",
            summary_date=date(2026, 5, 26),
            summary="別人摘要",
            language="en",
            created_at=datetime.now(timezone.utc),
        ),
    ]

    summaries = await consultation_service.get_all_summaries("U123")

    assert len(summaries) == 1
    assert summaries[0].summary == "5/26 摘要"


# 驗證 get_raw_view 能正確從對話紀錄庫（Chat History）撈出特定使用者的原始對話內容
@pytest.mark.asyncio
async def test_get_raw_view_returns_messages(
    consultation_service: ConsultationService,
):
    msg = ChatMessage(
        line_id="U123",
        message_type="text",
        content="肚子痛",
        timestamp=datetime.now(timezone.utc),
    )
    await consultation_service._chat_history_repository.append_message("U123", msg)

    messages = await consultation_service.get_raw_view(
        "U123", datetime.now(timezone.utc).date()
    )

    assert len(messages) == 1
    assert messages[0].content == "肚子痛"


# 呼叫 summarize 時，會使用經由 LLM 生成的文字，並成功寫入 Repo。
@pytest.mark.asyncio
async def test_summarize_uses_generated_text(
    consultation_service: ConsultationService,
):
    msg = ChatMessage(
        line_id="U123",
        message_type="text",
        content="今天肚子痛",
        timestamp=datetime(2026, 5, 17, 8, 0, tzinfo=timezone.utc),
    )
    await consultation_service._chat_history_repository.append_message("U123", msg)

    with patch.object(
        consultation_service,
        "_generate_summary",
        new=AsyncMock(return_value="摘要完成"),
    ):
        summary = await consultation_service.summarize(
            "U123", ConsultationSummarizeRequest(target_date=date.today())
        )

    assert summary.summary == "摘要完成"
    assert summary.language == "zh-TW"
    assert consultation_service._repository.summary is not None
    assert consultation_service._repository.summary.summary_date == date(2026, 5, 17)


@pytest.mark.asyncio
async def test_summarize_passes_user_language_into_prompt(
    consultation_service: ConsultationService,
):
    msg = ChatMessage(
        line_id="U123",
        message_type="text",
        content="我今天頭痛",
        timestamp=datetime(2026, 5, 17, 8, 0, tzinfo=timezone.utc),
    )
    await consultation_service._chat_history_repository.append_message("U123", msg)

    consultation_service._gemini_service.chat_model.ainvoke = AsyncMock(
        return_value=SimpleNamespace(content='{"主訴":"頭痛"}')
    )
    consultation_service._user_profile_service.language = "en"

    summary = await consultation_service.summarize(
        "U123", ConsultationSummarizeRequest(target_date=date.today(), force=True)
    )

    assert summary.language == "en"
    consultation_service._gemini_service.chat_model.ainvoke.assert_awaited_once()
    prompt = consultation_service._gemini_service.chat_model.ainvoke.call_args.args[0][
        0
    ].content
    assert "使用者資料庫語言：en（英文）" in prompt
    assert "JSON 欄位 key 請維持 schema 中的中文名稱" in prompt
    assert "請以該語言撰寫各欄位內容。" in prompt
