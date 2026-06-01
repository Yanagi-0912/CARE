from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.consultation import (
    ConsultationMessage,
    ConsultationSummary,
    ConsultationSummarizeRequest,
)
from app.services.consultation.context import (
    ConsultationContext,
    consultation_context_scope,
)
from app.services.consultation.consultation_service import ConsultationService


class FakeStore:
    def __init__(self) -> None:
        self.messages: dict[str, list[ConsultationMessage]] = {}

    async def append_message(self, line_id: str, message: ConsultationMessage) -> None:
        self.messages.setdefault(line_id, []).append(message)

    async def list_messages(self, line_id: str) -> list[ConsultationMessage]:
        return list(self.messages.get(line_id, []))

    async def list_dates(self, line_id: str) -> list[date]:
        return sorted(
            {message.timestamp.date() for message in self.messages.get(line_id, [])}
        )


class FakeRepository:
    def __init__(self) -> None:
        self.summary: ConsultationSummary | None = None
        self.summaries: list[ConsultationSummary] = []

    async def get_summary(self, line_id: str, target_date: date):
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


@pytest.fixture
def consultation_service() -> ConsultationService:
    fake_store = FakeStore()
    fake_repo = FakeRepository()
    fake_gemini = SimpleNamespace(_chat_llm=SimpleNamespace(ainvoke=AsyncMock()))
    return ConsultationService(
        store=fake_store, repository=fake_repo, gemini_service=fake_gemini
    )


@pytest.mark.asyncio
async def test_record_user_message_stores_context_metadata(
    consultation_service: ConsultationService,
):
    # 模擬 LINE image 訊息物件
    image_message = SimpleNamespace(type="image", id="M1")
    context = ConsultationContext(
        line_id="U123",
        message_type="image",
        event_time=datetime(2026, 5, 17, 8, 0, tzinfo=timezone.utc),
        raw_message=image_message,
    )

    with consultation_context_scope(context):
        result = await consultation_service.record_user_message(
            "這是一張紅疹照片的分析文字"
        )

    assert result.stored is True
    stored_messages = await consultation_service._store.list_messages("U123")
    assert stored_messages[0].message_type == "image"
    # raw_text 由 _normalize_raw_text 從 raw_message.id 提取
    assert stored_messages[0].raw_text == "M1"
    assert stored_messages[0].content == "這是一張紅疹照片的分析文字"
    assert stored_messages[0].timestamp == datetime(
        2026, 5, 17, 8, 0, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
async def test_record_assistant_message_stores_reply(
    consultation_service: ConsultationService,
):
    context = ConsultationContext(line_id="U123", message_type="text")

    with consultation_context_scope(context):
        result = await consultation_service.record_assistant_message(
            "請多喝水並觀察症狀"
        )

    assert result.stored is True
    stored_messages = await consultation_service._store.list_messages("U123")
    assert stored_messages[0].message_type == "assistant_reply"
    assert stored_messages[0].content == "請多喝水並觀察症狀"
    assert stored_messages[0].raw_text is None


@pytest.mark.asyncio
async def test_get_view_prefers_summary(
    consultation_service: ConsultationService,
):
    summary = ConsultationSummary(
        line_id="U123",
        summary_date=date(2026, 5, 17),
        summary="今天主要是腸胃不適",
        created_at=datetime.now(timezone.utc),
    )
    consultation_service._repository.summary = summary

    view = await consultation_service.get_view("U123", date(2026, 5, 17))

    assert view.view_type == "summary"
    assert view.summary == "今天主要是腸胃不適"
    assert view.messages == []


@pytest.mark.asyncio
async def test_get_view_without_summary_does_not_fallback_to_raw(
    consultation_service: ConsultationService,
):
    view = await consultation_service.get_view("U123")

    assert view.view_type == "summary"
    assert view.summary is None
    assert view.messages == []


@pytest.mark.asyncio
async def test_list_summary_history_returns_repository_data(
    consultation_service: ConsultationService,
):
    consultation_service._repository.summaries = [
        ConsultationSummary(
            line_id="U123",
            summary_date=date(2026, 5, 26),
            summary="5/26 摘要",
            created_at=datetime.now(timezone.utc),
        ),
        ConsultationSummary(
            line_id="U999",
            summary_date=date(2026, 5, 26),
            summary="別人摘要",
            created_at=datetime.now(timezone.utc),
        ),
    ]

    summaries = await consultation_service.get_all_summaries("U123")

    assert len(summaries) == 1
    assert summaries[0].summary == "5/26 摘要"


@pytest.mark.asyncio
async def test_get_raw_view_returns_messages(
    consultation_service: ConsultationService,
):
    context = ConsultationContext(line_id="U123", message_type="text")
    with consultation_context_scope(context):
        await consultation_service.record_user_message("肚子痛")

    view = await consultation_service.get_raw_view(
        "U123", datetime.now(timezone.utc).date()
    )

    assert view.view_type == "raw"
    assert len(view.messages) == 1
    assert view.messages[0].content == "肚子痛"


@pytest.mark.asyncio
async def test_summarize_uses_generated_text(
    consultation_service: ConsultationService,
):
    context = ConsultationContext(
        line_id="U123",
        message_type="text",
        event_time=datetime(2026, 5, 17, 8, 0, tzinfo=timezone.utc),
    )
    with consultation_context_scope(context):
        await consultation_service.record_user_message("今天肚子痛")

    with patch.object(
        consultation_service,
        "_generate_summary",
        new=AsyncMock(return_value="摘要完成"),
    ):
        summary = await consultation_service.summarize(
            "U123", ConsultationSummarizeRequest(target_date=date.today())
        )

    assert summary.summary == "摘要完成"
    assert consultation_service._repository.summary is not None
    assert consultation_service._repository.summary.summary_date == date(2026, 5, 17)


@pytest.mark.asyncio
async def test_summarize_without_target_date_includes_cross_midnight_messages(
    consultation_service: ConsultationService,
):
    context = ConsultationContext(line_id="U123", message_type="text")
    with consultation_context_scope(context):
        await consultation_service._store.append_message(
            "U123",
            ConsultationMessage(
                line_id="U123",
                message_type="text",
                content="23:59 的訊息",
                raw_text="23:59 的訊息",
                timestamp=datetime(2026, 5, 26, 23, 59, tzinfo=timezone.utc),
            ),
        )
        await consultation_service._store.append_message(
            "U123",
            ConsultationMessage(
                line_id="U123",
                message_type="text",
                content="00:00 的訊息",
                raw_text="00:00 的訊息",
                timestamp=datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc),
            ),
        )

    captured_messages: list[ConsultationMessage] = []

    async def fake_generate_summary(user_id: str, target_date: date, messages):
        captured_messages.extend(messages)
        return "跨午夜摘要"

    with patch.object(
        consultation_service,
        "_generate_summary",
        new=fake_generate_summary,
    ):
        summary = await consultation_service.summarize(
            "U123", ConsultationSummarizeRequest()
        )

    assert summary.summary == "跨午夜摘要"
    assert [message.content for message in captured_messages] == [
        "23:59 的訊息",
        "00:00 的訊息",
    ]


@pytest.mark.asyncio
async def test_summarize_ignores_target_date_and_uses_full_conversation(
    consultation_service: ConsultationService,
):
    context = ConsultationContext(line_id="U123", message_type="text")
    with consultation_context_scope(context):
        await consultation_service._store.append_message(
            "U123",
            ConsultationMessage(
                line_id="U123",
                message_type="text",
                content="5/26 的訊息",
                raw_text="5/26 的訊息",
                timestamp=datetime(2026, 5, 26, 23, 59, tzinfo=timezone.utc),
            ),
        )
        await consultation_service._store.append_message(
            "U123",
            ConsultationMessage(
                line_id="U123",
                message_type="text",
                content="5/27 的訊息",
                raw_text="5/27 的訊息",
                timestamp=datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc),
            ),
        )

    captured_messages: list[ConsultationMessage] = []

    async def fake_generate_summary(user_id: str, target_date: date, messages):
        captured_messages.extend(messages)
        return "忽略 target_date"

    with patch.object(
        consultation_service,
        "_generate_summary",
        new=fake_generate_summary,
    ):
        summary = await consultation_service.summarize(
            "U123", ConsultationSummarizeRequest(target_date=date(2026, 5, 26))
        )

    assert summary.summary == "忽略 target_date"
    assert summary.summary_date == date(2026, 5, 27)
    assert [message.content for message in captured_messages] == [
        "5/26 的訊息",
        "5/27 的訊息",
    ]


@pytest.mark.asyncio
async def test_summarize_handles_mixed_timezone_timestamps(
    consultation_service: ConsultationService,
):
    context = ConsultationContext(line_id="U123", message_type="text")
    with consultation_context_scope(context):
        await consultation_service._store.append_message(
            "U123",
            ConsultationMessage(
                line_id="U123",
                message_type="text",
                content="naive timestamp message",
                raw_text="naive timestamp message",
                timestamp=datetime(2026, 5, 28, 9, 30),
            ),
        )
        await consultation_service._store.append_message(
            "U123",
            ConsultationMessage(
                line_id="U123",
                message_type="text",
                content="aware timestamp message",
                raw_text="aware timestamp message",
                timestamp=datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc),
            ),
        )

    captured_messages: list[ConsultationMessage] = []

    async def fake_generate_summary(user_id: str, target_date: date, messages):
        captured_messages.extend(messages)
        return "mixed timezone summary"

    with patch.object(
        consultation_service,
        "_generate_summary",
        new=fake_generate_summary,
    ):
        summary = await consultation_service.summarize(
            "U123", ConsultationSummarizeRequest()
        )

    assert summary.summary == "mixed timezone summary"
    assert summary.summary_date == date(2026, 5, 28)
    assert [message.content for message in captured_messages] == [
        "naive timestamp message",
        "aware timestamp message",
    ]
