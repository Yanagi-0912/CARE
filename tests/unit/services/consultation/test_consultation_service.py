from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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
        self.messages: dict[date, list[ConsultationMessage]] = {}

    async def append_message(
        self, line_id: str, summary_date: date, message: ConsultationMessage
    ) -> None:
        self.messages.setdefault(summary_date, []).append(message)

    async def list_messages(
        self, line_id: str, summary_date: date
    ) -> list[ConsultationMessage]:
        return list(self.messages.get(summary_date, []))

    async def list_dates(self, line_id: str) -> list[date]:
        return sorted(self.messages)


class FakeRepository:
    def __init__(self) -> None:
        self.summary: ConsultationSummary | None = None

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
    stored_messages = await consultation_service._store.list_messages(
        "U123", date(2026, 5, 17)
    )
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
    stored_messages = await consultation_service._store.list_messages(
        "U123", datetime.now(timezone.utc).date()
    )
    assert stored_messages[0].message_type == "assistant_reply"
    assert stored_messages[0].content == "請多喝水並觀察症狀"


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
    monkeypatch,
):
    context = ConsultationContext(line_id="U123", message_type="text")
    with consultation_context_scope(context):
        await consultation_service.record_user_message("今天肚子痛")

    monkeypatch.setattr(
        consultation_service, "_generate_summary", AsyncMock(return_value="摘要完成")
    )

    summary = await consultation_service.summarize(
        "U123", ConsultationSummarizeRequest(target_date=date.today())
    )

    assert summary.summary == "摘要完成"
    assert consultation_service._repository.summary is not None
