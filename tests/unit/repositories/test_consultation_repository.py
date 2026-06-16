from unittest.mock import AsyncMock, MagicMock
from datetime import date, datetime, timezone

import pytest

from app.repositories.consultation_repository import ConsultationRepository
from app.models.consultation import ConsultationSummary


@pytest.mark.asyncio
async def test_ensure_indexes_drops_ttl_and_creates_compound_index():
    collection = AsyncMock()

    # 呼叫 ensure_indexes 並傳入 mock collection 進行依賴注入
    await ConsultationRepository.ensure_indexes(collection=collection)

    # 驗證是否嘗試防禦性地刪除 TTL 索引，並建立複合索引
    collection.drop_index.assert_called_once_with(
        "consultation_summary_created_at_ttl"
    )
    collection.create_index.assert_called_once_with(
        [("line_id", 1), ("summary_date", -1)],
        name="consultation_summary_line_id_date",
    )


@pytest.mark.asyncio
async def test_list_summaries_returns_sorted_user_history():
    collection = MagicMock()
    cursor = MagicMock()
    collection.find.return_value = cursor
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(
        return_value=[
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
    )

    # 呼叫 get_all_summaries 並傳入 mock collection 進行依賴注入
    summaries = await ConsultationRepository.get_all_summaries(
        "U123", collection=collection
    )

    collection.find.assert_called_once_with({"line_id": "U123"})
    cursor.sort.assert_called_once_with("summary_date", -1)
    cursor.to_list.assert_awaited_once_with(length=None)
    assert [summary.summary for summary in summaries] == ["5/26 摘要", "5/25 摘要"]


@pytest.mark.asyncio
async def test_upsert_summary_limit_capping_removes_oldest():
    collection = MagicMock()
    cursor = MagicMock()
    collection.find.return_value = cursor
    cursor.sort.return_value = cursor

    # 模擬資料庫中有 21 筆資料的 ID，第 21 筆 (最舊) 的 ID 是 "mongo-id-21"
    docs = [{"_id": f"mongo-id-{i}"} for i in range(1, 22)]
    cursor.to_list = AsyncMock(return_value=docs)
    collection.update_one = AsyncMock()
    collection.delete_many = AsyncMock()

    summary = ConsultationSummary(
        line_id="U123",
        summary_date=date(2026, 5, 26),
        summary="測試摘要",
        created_at=datetime.now(timezone.utc),
    )

    # 呼叫 upsert_summary 並傳入 mock collection 進行依賴注入
    await ConsultationRepository.upsert_summary(summary, collection=collection)

    collection.update_one.assert_called_once()
    collection.find.assert_called_once_with({"line_id": "U123"}, {"_id": 1})
    cursor.sort.assert_called_once_with("summary_date", -1)
    cursor.to_list.assert_awaited_once_with(length=None)

    # 驗證是否有呼叫 delete_many 刪除第 20 個索引之後的 ID（即 "mongo-id-21"）
    collection.delete_many.assert_called_once_with({"_id": {"$in": ["mongo-id-21"]}})


@pytest.mark.asyncio
async def test_upsert_summary_does_not_delete_when_under_limit():
    collection = MagicMock()
    cursor = MagicMock()
    collection.find.return_value = cursor
    cursor.sort.return_value = cursor

    # 模擬資料庫中只有 15 筆資料
    docs = [{"_id": f"mongo-id-{i}"} for i in range(1, 16)]
    cursor.to_list = AsyncMock(return_value=docs)
    collection.update_one = AsyncMock()
    collection.delete_many = AsyncMock()

    summary = ConsultationSummary(
        line_id="U123",
        summary_date=date(2026, 5, 26),
        summary="測試摘要",
        created_at=datetime.now(timezone.utc),
    )

    # 呼叫 upsert_summary 並傳入 mock collection 進行依賴注入
    await ConsultationRepository.upsert_summary(summary, collection=collection)

    collection.update_one.assert_called_once()
    # 筆數未滿 20 筆，不應進行刪除
    collection.delete_many.assert_not_called()
