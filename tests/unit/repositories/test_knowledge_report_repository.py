from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.knowledge_report_repository import KnowledgeReportRepository


@pytest.mark.asyncio
async def test_delete_pending_or_reviewing_by_urls():
    collection = MagicMock()
    collection.delete_many = AsyncMock(return_value=MagicMock(deleted_count=2))
    urls = [
        "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1",
        "https://www.cdc.gov.tw/Category/Page/xxx",
    ]

    deleted = await KnowledgeReportRepository.delete_pending_or_reviewing_by_urls(
        urls, collection=collection
    )

    assert deleted == 2
    collection.delete_many.assert_awaited_once_with(
        {
            "status": {"$in": ["pending", "reviewing"]},
            "user_source_urls": {"$in": urls},
        }
    )


@pytest.mark.asyncio
async def test_delete_pending_or_reviewing_by_urls_empty_noop():
    collection = MagicMock()
    collection.delete_many = AsyncMock()

    deleted = await KnowledgeReportRepository.delete_pending_or_reviewing_by_urls(
        [], collection=collection
    )

    assert deleted == 0
    collection.delete_many.assert_not_awaited()
