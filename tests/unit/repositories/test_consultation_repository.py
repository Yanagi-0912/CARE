from unittest.mock import AsyncMock

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
