from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.knowledge_report import ContentPreview, ContentPreviewItem
from app.repositories.knowledge_report_preview_repository import (
    KnowledgeReportPreviewRepository,
)

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
REPORT_ID = "KR-20260816-AB12"
URL = "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1"


def _preview(**overrides) -> ContentPreview:
    payload = {
        "preview_id": "PV-0001",
        "report_id": REPORT_ID,
        "status": "ready",
        "items": [
            ContentPreviewItem(
                url=URL,
                status="ok",
                title="高血壓防治",
                content="內容",
                content_hash="a" * 64,
                char_count=2,
            )
        ],
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=60),
    }
    payload.update(overrides)
    return ContentPreview(**payload)


@pytest.mark.asyncio
async def test_ensure_indexes_creates_unique_report_id_and_ttl_on_expires_at():
    collection = MagicMock()
    collection.create_index = AsyncMock()

    await KnowledgeReportPreviewRepository.ensure_indexes(collection=collection)

    calls = collection.create_index.await_args_list
    unique_call = next(c for c in calls if c.args[0] == [("report_id", 1)])
    assert unique_call.kwargs["unique"] is True

    ttl_call = next(c for c in calls if c.args[0] == [("expires_at", 1)])
    # TTL 索引要以 expireAfterSeconds=0 讓 Mongo 依 expires_at 本身的時間清除；
    # 少了這個關鍵字就只是一個普通索引，快照永遠不會被回收。
    assert ttl_call.kwargs["expireAfterSeconds"] == 0


@pytest.mark.asyncio
async def test_upsert_for_report_replaces_previous_preview_of_same_report():
    collection = MagicMock()
    collection.replace_one = AsyncMock(return_value=MagicMock(matched_count=1))
    preview = _preview()

    await KnowledgeReportPreviewRepository.upsert_for_report(
        preview, collection=collection
    )

    collection.replace_one.assert_awaited_once()
    filter_arg, replacement = collection.replace_one.await_args.args[:2]
    # 以 report_id 為鍵整份取代：同一筆回報只留最新的一份預覽
    assert filter_arg == {"report_id": REPORT_ID}
    assert collection.replace_one.await_args.kwargs["upsert"] is True
    assert replacement["preview_id"] == "PV-0001"


@pytest.mark.asyncio
async def test_upsert_for_report_stores_datetimes_as_bson_dates():
    """expires_at 必須存成 BSON date，否則 TTL 索引與過期比較都失效。

    存成 ISO 字串時 Mongo 的 TTL monitor 會略過該文件（不是 date 型別），
    而 find_ready 的 $gt 比較在 BSON 型別排序下 String 恆大於 Date，
    會讓「已過期的快照」永遠被判定為仍然有效。
    """
    collection = MagicMock()
    collection.replace_one = AsyncMock(return_value=MagicMock(matched_count=1))

    await KnowledgeReportPreviewRepository.upsert_for_report(
        _preview(), collection=collection
    )

    replacement = collection.replace_one.await_args.args[1]
    assert isinstance(replacement["created_at"], datetime)
    assert isinstance(replacement["expires_at"], datetime)


@pytest.mark.asyncio
async def test_find_by_report_id_queries_by_report_id_and_returns_model():
    collection = MagicMock()
    document = _preview().model_dump()
    document["_id"] = "mongo-object-id"
    collection.find_one = AsyncMock(return_value=document)

    found = await KnowledgeReportPreviewRepository.find_by_report_id(
        REPORT_ID, collection=collection
    )

    collection.find_one.assert_awaited_once_with({"report_id": REPORT_ID})
    assert found is not None
    assert found.preview_id == "PV-0001"
    assert found.items[0].url == URL


@pytest.mark.asyncio
async def test_find_by_report_id_returns_none_when_absent():
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=None)

    found = await KnowledgeReportPreviewRepository.find_by_report_id(
        REPORT_ID, collection=collection
    )

    assert found is None


@pytest.mark.asyncio
async def test_finish_binds_preview_id_so_superseded_results_are_discarded():
    collection = MagicMock()
    collection.replace_one = AsyncMock(return_value=MagicMock(matched_count=0))

    applied = await KnowledgeReportPreviewRepository.finish(
        _preview(), collection=collection
    )

    # filter 綁 preview_id：期間若已被更新的預覽取代就不會命中，本次結果丟棄
    filter_arg = collection.replace_one.await_args.args[0]
    assert filter_arg == {"report_id": REPORT_ID, "preview_id": "PV-0001"}
    assert collection.replace_one.await_args.kwargs.get("upsert", False) is False
    assert applied is False


@pytest.mark.asyncio
async def test_finish_returns_true_when_preview_still_current():
    collection = MagicMock()
    collection.replace_one = AsyncMock(return_value=MagicMock(matched_count=1))

    applied = await KnowledgeReportPreviewRepository.finish(
        _preview(), collection=collection
    )

    assert applied is True


@pytest.mark.asyncio
async def test_find_ready_filters_by_ready_status_and_unexpired():
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=None)

    await KnowledgeReportPreviewRepository.find_ready(
        REPORT_ID, now=NOW, collection=collection
    )

    collection.find_one.assert_awaited_once_with(
        {
            "report_id": REPORT_ID,
            "status": "ready",
            "expires_at": {"$gt": NOW},
        }
    )
