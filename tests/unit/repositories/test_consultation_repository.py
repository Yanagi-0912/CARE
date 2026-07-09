from unittest.mock import AsyncMock, MagicMock
from datetime import date, datetime, timezone

import pytest

from app.repositories.consultation_repository import ConsultationRepository
from app.models.consultation import ConsultationSummary


# 測試正確地從資料庫中取得使用者的所有摘要，並按日期排序
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


# 測試 upsert_summary 方法在資料庫中已經有 20 筆資料時，會刪除最舊的一筆資料
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
async def test_get_latest_summary_returns_newest_record():
    collection = MagicMock()
    cursor = MagicMock()
    collection.find.return_value = cursor
    cursor.sort.return_value = cursor

    # limit(1).to_list(length=1) 模擬回傳排在最前面(最新)的一筆資料
    cursor.limit.return_value = cursor
    fixed_now = datetime(2026, 5, 26, 15, 0, tzinfo=timezone.utc)
    cursor.to_list = AsyncMock(
        return_value=[
            {
                "line_id": "U123",
                "summary_date": "2026-05-26",
                "summary": "最新一筆摘要",
                "created_at": fixed_now,
                "_id": "mongo-id-latest",
            }
        ]
    )

    latest_summary = await ConsultationRepository.get_latest_summary(
        "U123", collection=collection
    )

    # 驗證查詢條件與排序、限制筆數的連鎖呼叫
    collection.find.assert_called_once_with({"line_id": "U123"})
    cursor.sort.assert_called_once_with("summary_date", -1)
    cursor.limit.assert_called_once_with(1)
    cursor.to_list.assert_awaited_once_with(length=1)

    # 驗證回傳物件與內容正確性，且已剔除 _id 欄位
    assert latest_summary is not None
    assert latest_summary.summary == "最新一筆摘要"
    assert latest_summary.summary_date == date(2026, 5, 26)


@pytest.mark.asyncio
async def test_get_latest_summary_returns_none_when_empty():
    collection = MagicMock()
    cursor = MagicMock()
    collection.find.return_value = cursor
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[])  # 模擬沒資料

    latest_summary = await ConsultationRepository.get_latest_summary(
        "U123", collection=collection
    )

    assert latest_summary is None


# 測試upsert summary會去更新該日期的那一筆摘要
@pytest.mark.asyncio
async def test_upsert_summary_builds_expected_update_query():
    collection = MagicMock()
    cursor = MagicMock()
    collection.find.return_value = cursor
    cursor.sort.return_value = cursor

    # 模擬資料庫狀況（未滿 20 筆，不觸發刪除）
    collection.update_one = AsyncMock()
    cursor.to_list = AsyncMock(return_value=[])

    fixed_now = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
    summary = ConsultationSummary(
        line_id="U123",
        summary_date=date(2026, 5, 26),
        summary="測試摘要內容",
        created_at=fixed_now,
    )

    await ConsultationRepository.upsert_summary(summary, collection=collection)

    # 核心驗證：確保 query filter 使用 isoformat 欄位，且 $set payload 結構正確
    collection.update_one.assert_called_once_with(
        {
            "line_id": "U123",
            "summary_date": "2026-05-26",
        },
        {
            "$set": {
                "line_id": "U123",
                "summary_date": "2026-05-26",
                "summary": "測試摘要內容",
                "created_at": fixed_now,
            }
        },
        upsert=True,
    )
