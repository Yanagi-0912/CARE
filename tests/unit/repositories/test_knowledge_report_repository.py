from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.knowledge_report import IngestJob
from app.repositories.knowledge_report_repository import KnowledgeReportRepository

STARTED_AT = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
STALE_BEFORE = STARTED_AT - timedelta(minutes=10)


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


def _cursor_collection(documents: list[dict]) -> tuple[MagicMock, MagicMock]:
    """回傳 (collection, cursor)；cursor 的 sort/skip/limit 皆鏈式回傳自身。"""
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.skip.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=documents)
    collection = MagicMock()
    collection.find.return_value = cursor
    return collection, cursor


@pytest.mark.asyncio
async def test_list_by_statuses_applies_limit_and_offset():
    collection, cursor = _cursor_collection([])

    await KnowledgeReportRepository.list_by_statuses(
        ["pending"], collection=collection, limit=20, offset=40
    )

    collection.find.assert_called_once_with({"status": {"$in": ["pending"]}})
    cursor.sort.assert_called_once_with("created_at", -1)
    cursor.skip.assert_called_once_with(40)
    cursor.limit.assert_called_once_with(20)
    cursor.to_list.assert_awaited_once_with(length=20)


@pytest.mark.asyncio
async def test_list_by_statuses_without_pagination_fetches_all():
    collection, cursor = _cursor_collection([])

    await KnowledgeReportRepository.list_by_statuses(["pending"], collection=collection)

    cursor.skip.assert_not_called()
    cursor.limit.assert_not_called()
    cursor.to_list.assert_awaited_once_with(length=None)


@pytest.mark.asyncio
async def test_count_by_statuses():
    collection = MagicMock()
    collection.count_documents = AsyncMock(return_value=7)

    total = await KnowledgeReportRepository.count_by_statuses(
        ["pending", "reviewing"], collection=collection
    )

    assert total == 7
    collection.count_documents.assert_awaited_once_with(
        {"status": {"$in": ["pending", "reviewing"]}}
    )


@pytest.mark.asyncio
async def test_start_ingest_job_filter_excludes_closed_and_live_jobs():
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    job = IngestJob(selected_urls=["u"], status="running", started_at=STARTED_AT)

    acquired = await KnowledgeReportRepository.start_ingest_job(
        report_id="KR-1",
        job=job,
        stale_before=STALE_BEFORE,
        collection=collection,
    )

    assert acquired is True
    filter_arg, update_arg = collection.update_one.await_args.args
    assert filter_arg["report_id"] == "KR-1"
    assert filter_arg["status"] == {"$nin": ["resolved", "rejected"]}
    # 三種「沒有進行中的新鮮工作」：無 job、非 running、已逾時
    assert filter_arg["$or"] == [
        {"ingest_job": None},
        {"ingest_job.status": {"$ne": "running"}},
        {"ingest_job.started_at": {"$lt": STALE_BEFORE}},
    ]
    # 未帶備註時不得寫入該欄位，否則會清掉原值
    assert "reviewer_note" not in update_arg["$set"]
    assert "resolution" not in update_arg["$set"]


@pytest.mark.asyncio
async def test_start_ingest_job_writes_notes_when_provided():
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    job = IngestJob(selected_urls=["u"], status="running", started_at=STARTED_AT)

    await KnowledgeReportRepository.start_ingest_job(
        report_id="KR-1",
        job=job,
        stale_before=STALE_BEFORE,
        reviewer_note="備註",
        resolution="結論",
        collection=collection,
    )

    _, update_arg = collection.update_one.await_args.args
    assert update_arg["$set"]["reviewer_note"] == "備註"
    assert update_arg["$set"]["resolution"] == "結論"


@pytest.mark.asyncio
async def test_start_ingest_job_returns_false_when_not_matched():
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=MagicMock(matched_count=0))
    job = IngestJob(selected_urls=["u"], status="running", started_at=STARTED_AT)

    acquired = await KnowledgeReportRepository.start_ingest_job(
        report_id="KR-1",
        job=job,
        stale_before=STALE_BEFORE,
        collection=collection,
    )

    assert acquired is False


@pytest.mark.asyncio
async def test_finish_ingest_job_binds_to_started_at():
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    job = IngestJob(
        selected_urls=["u"],
        status="succeeded",
        started_at=STARTED_AT,
        finished_at=STARTED_AT,
    )

    applied = await KnowledgeReportRepository.finish_ingest_job(
        report_id="KR-1",
        started_at=STARTED_AT,
        report_status="resolved",
        job=job,
        collection=collection,
    )

    assert applied is True
    filter_arg, update_arg = collection.update_one.await_args.args
    # 綁住本次工作，期間被拒絕或重新 approve 就不命中
    assert filter_arg == {
        "report_id": "KR-1",
        "ingest_job.status": "running",
        "ingest_job.started_at": STARTED_AT,
    }
    # 只碰 ingest 相關欄位，不整份覆寫
    assert set(update_arg["$set"]) == {"status", "ingest_job", "updated_at"}


@pytest.mark.asyncio
async def test_finish_ingest_job_returns_false_when_not_matched():
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=MagicMock(matched_count=0))
    job = IngestJob(selected_urls=["u"], status="failed", started_at=STARTED_AT)

    applied = await KnowledgeReportRepository.finish_ingest_job(
        report_id="KR-1",
        started_at=STARTED_AT,
        report_status="reviewing",
        job=job,
        collection=collection,
    )

    assert applied is False


@pytest.mark.asyncio
async def test_ensure_indexes_covers_admin_queue_query():
    collection = MagicMock()
    collection.create_index = AsyncMock()

    await KnowledgeReportRepository.ensure_indexes(collection=collection)

    keys = [call.args[0] for call in collection.create_index.await_args_list]
    assert [("status", 1), ("created_at", -1)] in keys


@pytest.mark.asyncio
async def test_count_by_statuses_empty_noop():
    collection = MagicMock()
    collection.count_documents = AsyncMock()

    assert await KnowledgeReportRepository.count_by_statuses([], collection=collection) == 0
    collection.count_documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_started_at_written_and_filtered_with_same_type():
    """寫入的 started_at 與 finish／逾時判定所比對的必須是同一種型別。

    這條不變式沒被守住的話，兩件事會同時壞掉，而且兩者都無聲：

    1. `finish_ingest_job` 的 filter 永遠不命中 → ingest 結果永遠被丟棄、
       工作永遠停在 running（違反規格「SHALL 記錄執行狀態與起訖時間」）。
    2. `_no_live_ingest_job` 的 `$lt` 拿 datetime 比字串 → BSON 型別排序裡
       String 高於 Date，條件恆為 false → 逾時的孤兒工作永遠無法被取代
       （違反規格「逾時的孤兒工作可重跑」）。

    兩者相加，同一筆回報第一次核准之後就再也無法核准。既有測試只斷言
    「傳進去的參數長什麼樣」，跨方法的型別一致性沒有任何一條測試涵蓋。
    """
    job = IngestJob(selected_urls=["https://www.hpa.gov.tw/x"], status="running",
                    started_at=STARTED_AT)

    start_collection = MagicMock()
    start_collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    await KnowledgeReportRepository.start_ingest_job(
        report_id="KR-1", job=job, stale_before=STALE_BEFORE,
        collection=start_collection,
    )
    stored = start_collection.update_one.await_args[0][1]["$set"]["ingest_job"]["started_at"]

    finish_collection = MagicMock()
    finish_collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    await KnowledgeReportRepository.finish_ingest_job(
        report_id="KR-1", started_at=STARTED_AT, report_status="resolved",
        job=job, collection=finish_collection,
    )
    filtered = finish_collection.update_one.await_args[0][0]["ingest_job.started_at"]

    assert type(stored) is type(filtered), (
        f"寫入的是 {type(stored).__name__}、比對的是 {type(filtered).__name__}——"
        "Mongo 不會做跨型別相等比較，這個 filter 永遠不會命中"
    )
    assert stored == filtered

    stale_cond = KnowledgeReportRepository._no_live_ingest_job(STALE_BEFORE)
    stale_value = stale_cond["$or"][2]["ingest_job.started_at"]["$lt"]
    assert type(stored) is type(stale_value), (
        f"寫入的是 {type(stored).__name__}、逾時判定比的是 {type(stale_value).__name__}——"
        "BSON 型別排序下 String 恆大於 Date，$lt 永遠為 false"
    )
