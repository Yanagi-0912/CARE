from unittest.mock import AsyncMock, MagicMock
from datetime import date

import pytest

from app.repositories.consultation_repository import (
    SUMMARY_TTL_SECONDS,
    ConsultationRepository,
)


@pytest.mark.asyncio
async def test_ensure_indexes_creates_summary_ttl_index(monkeypatch):
    collection = AsyncMock()
    monkeypatch.setattr(
        "app.repositories.consultation_repository.MongoDBManager.get_consultation_summaries_collection",
        lambda: collection,
    )

    await ConsultationRepository.ensure_indexes()

    collection.create_index.assert_awaited_once_with(
        [("created_at", 1)],
        expireAfterSeconds=SUMMARY_TTL_SECONDS,
        name="consultation_summary_created_at_ttl",
    )


@pytest.mark.asyncio
async def test_list_summaries_returns_sorted_user_history(monkeypatch):
    collection = MagicMock()
    cursor = AsyncMock()
    collection.find.return_value = cursor
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list.return_value = [
        {
            "line_id": "U123",
            "summary_date": date(2026, 5, 26),
            "summary": "5/26 摘要",
            "created_at": date(2026, 5, 26),
            "_id": "mongo-id-1",
        },
        {
            "line_id": "U123",
            "summary_date": date(2026, 5, 25),
            "summary": "5/25 摘要",
            "created_at": date(2026, 5, 25),
            "_id": "mongo-id-2",
        },
    ]
    monkeypatch.setattr(
        "app.repositories.consultation_repository.MongoDBManager.get_consultation_summaries_collection",
        lambda: collection,
    )

    summaries = await ConsultationRepository.get_all_summaries("U123")

    collection.find.assert_called_once_with({"line_id": "U123"})
    cursor.sort.assert_called_once_with("summary_date", -1)
    cursor.to_list.assert_awaited_once_with(length=None)
    assert [summary.summary for summary in summaries] == ["5/26 摘要", "5/25 摘要"]
