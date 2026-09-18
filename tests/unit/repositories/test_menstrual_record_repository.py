from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.health import MenstrualRecord
from app.repositories.menstrual_record_repository import MenstrualRecordRepository


def _collection() -> MagicMock:
    collection = MagicMock()
    collection.create_index = AsyncMock()
    collection.insert_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=None)
    collection.update_one = AsyncMock()
    collection.delete_one = AsyncMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=[])
    collection.find = MagicMock(return_value=cursor)
    return collection


@pytest.mark.asyncio
async def test_ensure_indexes_creates_user_start_date_index():
    collection = _collection()

    await MenstrualRecordRepository.ensure_indexes(collection=collection)

    (index_spec,), _ = collection.create_index.call_args
    assert index_spec == [("user_id", 1), ("start_date", -1)]


@pytest.mark.asyncio
async def test_add_assigns_id_and_never_persists_computed_fields():
    collection = _collection()
    record = MenstrualRecord(user_id="U1", start_date="2026-09-01")

    created = await MenstrualRecordRepository.add(record, collection=collection)

    assert created.id
    (document,), _ = collection.insert_one.call_args
    assert document["_id"] == created.id
    assert "cycle_length_days" not in document
    assert "period_length_days" not in document


@pytest.mark.asyncio
async def test_list_by_user_sorts_by_start_date_descending_and_caps_at_200():
    """menstrual-cycle-log spec「週期資訊由後端計算」附近的查詢上限
    （constraints.md「狀態碼」：單次回應至多 200 筆，同血壓血糖量測）。"""
    collection = _collection()

    await MenstrualRecordRepository.list_by_user("U1", collection=collection)

    (query,), _ = collection.find.call_args
    assert query == {"user_id": "U1"}
    collection.find.return_value.sort.assert_called_once_with("start_date", -1)
    collection.find.return_value.sort.return_value.limit.assert_called_once_with(200)


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_missing():
    collection = _collection()

    result = await MenstrualRecordRepository.get_by_id("MISSING", collection=collection)

    assert result is None


@pytest.mark.asyncio
async def test_update_sets_fields_and_stamps_updated_at():
    collection = _collection()
    collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    collection.find_one = AsyncMock(
        return_value={
            "_id": "R1",
            "user_id": "U1",
            "start_date": "2026-09-01",
            "end_date": "2026-09-05",
        }
    )

    updated = await MenstrualRecordRepository.update(
        "R1", {"end_date": "2026-09-05"}, collection=collection
    )

    assert updated is not None
    assert updated.end_date == "2026-09-05"
    (query, update), _ = collection.update_one.call_args
    assert query == {"_id": "R1"}
    assert update["$set"]["end_date"] == "2026-09-05"
    assert "updated_at" in update["$set"]


@pytest.mark.asyncio
async def test_update_returns_none_when_record_missing():
    collection = _collection()
    collection.update_one = AsyncMock(return_value=MagicMock(matched_count=0))

    updated = await MenstrualRecordRepository.update(
        "MISSING", {"note": "x"}, collection=collection
    )

    assert updated is None


@pytest.mark.asyncio
async def test_delete_returns_true_when_removed():
    collection = _collection()
    collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))

    deleted = await MenstrualRecordRepository.delete("R1", collection=collection)

    assert deleted is True
    collection.delete_one.assert_awaited_once_with({"_id": "R1"})


@pytest.mark.asyncio
async def test_find_overlapping_queries_only_by_user_and_excludes_self():
    """查詢本身只依 user_id 篩選（精確的區間相交判定改在應用層做——見下方
    ``_FakeMenstrualCollection`` 系列測試；一份使用者的經期紀錄量不大，全部
    取回逐筆比對，換來的是「既有進行中的紀錄該不該視為佔滿 15 天」這種
    無法用 Mongo 對字串日期做算術的規則能夠正確表達）。``exclude_id`` 用於
    更新時排除自己。"""
    collection = _collection()

    await MenstrualRecordRepository.find_overlapping(
        "U1",
        start_date="2026-09-01",
        end_date="2026-09-05",
        exclude_id="R_SELF",
        collection=collection,
    )

    (query,), _ = collection.find.call_args
    assert query == {"user_id": "U1", "_id": {"$ne": "R_SELF"}}


@pytest.mark.asyncio
async def test_find_overlapping_caps_the_candidate_query_at_200():
    """Task 9 修復：``find_overlapping`` 原本是本次 change 裡唯一一個沒有
    加上限的查詢（``to_list(length=None)`` 前面沒有 ``.limit()``）——同
    ``list_by_user``（constraints.md「狀態碼」：單次回應至多 200 筆）加上
    同樣的上限。"""
    collection = _collection()

    await MenstrualRecordRepository.find_overlapping(
        "U1", start_date="2026-09-01", end_date="2026-09-05", collection=collection
    )

    collection.find.return_value.limit.assert_called_once_with(200)


class _FakeMenstrualCollection:
    """真的會依查詢過濾的假集合，用來證明重疊判定的區間相交邏輯正確，而不
    只是驗證查詢字典的長相（那件事上面已經驗證過）。查詢本身只依 ``user_id``
    （與可能的 ``_id`` 排除）篩選，精確的區間相交判定在 repository 內以
    Python 進行——這裡的假集合只需要模擬這兩個條件。"""

    def __init__(self, docs):
        self.docs = docs

    def find(self, query):
        exclude_id = None
        if "_id" in query and "$ne" in query["_id"]:
            exclude_id = query["_id"]["$ne"]
        matched = [
            doc
            for doc in self.docs
            if doc["user_id"] == query["user_id"] and doc.get("_id") != exclude_id
        ]
        cursor = MagicMock()
        cursor.limit = MagicMock(return_value=cursor)
        cursor.to_list = AsyncMock(return_value=matched)
        return cursor


@pytest.mark.asyncio
async def test_find_overlapping_matches_a_start_date_falling_inside_an_existing_period():
    """menstrual-cycle-log spec「經期重疊」：新開始日期落在既有經期的
    開始與結束日期之間。"""
    collection = _FakeMenstrualCollection(
        [
            {
                "_id": "R1",
                "user_id": "U1",
                "start_date": "2026-09-01",
                "end_date": "2026-09-15",
            }
        ]
    )

    overlapping = await MenstrualRecordRepository.find_overlapping(
        "U1", start_date="2026-09-10", end_date=None, collection=collection
    )

    assert [r.id for r in overlapping] == ["R1"]


@pytest.mark.asyncio
async def test_find_overlapping_excludes_the_record_being_updated():
    """PATCH 重新檢查重疊時排除自己：即使日期完全相同也不算跟自己重疊。"""
    collection = _FakeMenstrualCollection(
        [
            {
                "_id": "R_SELF",
                "user_id": "U1",
                "start_date": "2026-09-01",
                "end_date": "2026-09-05",
            }
        ]
    )

    overlapping = await MenstrualRecordRepository.find_overlapping(
        "U1",
        start_date="2026-09-01",
        end_date="2026-09-05",
        exclude_id="R_SELF",
        collection=collection,
    )

    assert overlapping == []


@pytest.mark.asyncio
async def test_find_overlapping_detects_real_overlap_with_an_ongoing_period_within_fifteen_days():
    """既有一筆還在進行中（沒有 end_date）的經期，視為佔滿 [start, start+15]
    （dispatch notes：「這是最長的合法經期天數」）；新開始日期落在這個窗口
    內即算重疊。"""
    collection = _FakeMenstrualCollection(
        [
            {
                "_id": "R_ONGOING",
                "user_id": "U1",
                "start_date": "2026-09-01",
                "end_date": None,
            }
        ]
    )

    overlapping = await MenstrualRecordRepository.find_overlapping(
        "U1", start_date="2026-09-10", end_date=None, collection=collection
    )

    assert [r.id for r in overlapping] == ["R_ONGOING"]


@pytest.mark.asyncio
async def test_find_overlapping_ignores_a_stale_ongoing_period_more_than_fifteen_days_old():
    """上個月忘記填結束日期的紀錄 SHALL NOT 擋住這個月的新紀錄（dispatch
    notes：「last month's forgotten-open record does not block this
    month」）——沒有 end_date 的既有紀錄只佔滿 [start, start+15]，不是
    佔到無限遠的未來。"""
    collection = _FakeMenstrualCollection(
        [
            {
                "_id": "R_STALE",
                "user_id": "U1",
                "start_date": "2026-08-01",
                "end_date": None,
            }
        ]
    )

    overlapping = await MenstrualRecordRepository.find_overlapping(
        "U1", start_date="2026-09-01", end_date=None, collection=collection
    )

    assert overlapping == []


@pytest.mark.asyncio
async def test_find_overlapping_treats_a_new_open_record_as_occupying_fifteen_days():
    """新紀錄自己沒有 end_date 時，同樣視為佔滿 [start, start+15]——即使既有
    紀錄本身有明確的結束日期，只要落在這個窗口內就算重疊。"""
    collection = _FakeMenstrualCollection(
        [
            {
                "_id": "R_LATER",
                "user_id": "U1",
                "start_date": "2026-09-14",
                "end_date": "2026-09-18",
            }
        ]
    )

    overlapping = await MenstrualRecordRepository.find_overlapping(
        "U1", start_date="2026-09-01", end_date=None, collection=collection
    )

    assert [r.id for r in overlapping] == ["R_LATER"]


@pytest.mark.asyncio
async def test_find_overlapping_returns_empty_for_a_non_overlapping_period():
    collection = _FakeMenstrualCollection(
        [
            {
                "_id": "R_PAST",
                "user_id": "U1",
                "start_date": "2026-08-01",
                "end_date": "2026-08-05",
            }
        ]
    )

    overlapping = await MenstrualRecordRepository.find_overlapping(
        "U1", start_date="2026-09-01", end_date="2026-09-05", collection=collection
    )

    assert overlapping == []
