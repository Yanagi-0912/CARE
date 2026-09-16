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
async def test_list_by_user_sorts_by_start_date_descending():
    collection = _collection()

    await MenstrualRecordRepository.list_by_user("U1", collection=collection)

    (query,), _ = collection.find.call_args
    assert query == {"user_id": "U1"}
    collection.find.return_value.sort.assert_called_once_with("start_date", -1)


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
async def test_find_overlapping_matches_a_start_date_falling_inside_an_existing_period():
    """menstrual-cycle-log spec「經期重疊」：新開始日期落在既有經期的
    開始與結束日期之間。"""
    collection = _collection()

    await MenstrualRecordRepository.find_overlapping(
        "U1", start_date="2026-09-10", end_date=None, collection=collection
    )

    (query,), _ = collection.find.call_args
    assert query == {
        "user_id": "U1",
        "start_date": {"$lte": "2026-09-10"},
        "$or": [{"end_date": None}, {"end_date": {"$gte": "2026-09-10"}}],
    }


@pytest.mark.asyncio
async def test_find_overlapping_excludes_the_record_being_updated():
    collection = _collection()

    await MenstrualRecordRepository.find_overlapping(
        "U1",
        start_date="2026-09-01",
        end_date="2026-09-05",
        exclude_id="R_SELF",
        collection=collection,
    )

    (query,), _ = collection.find.call_args
    assert query["_id"] == {"$ne": "R_SELF"}
    assert query["start_date"] == {"$lte": "2026-09-05"}


class _FakeMenstrualCollection:
    """真的會依查詢過濾的假集合，用來證明重疊查詢的判定邏輯正確，而不只是
    驗證查詢字典的長相（那件事上面兩個測試已經驗證過）。"""

    def __init__(self, docs):
        self.docs = docs

    def find(self, query):
        matched = [doc for doc in self.docs if self._matches(doc, query)]
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=matched)
        return cursor

    @classmethod
    def _matches(cls, doc, query):
        for key, condition in query.items():
            if key == "$or":
                if not any(cls._matches(doc, clause) for clause in condition):
                    return False
            elif key == "_id" and isinstance(condition, dict) and "$ne" in condition:
                if doc.get("_id") == condition["$ne"]:
                    return False
            elif isinstance(condition, dict):
                for op, value in condition.items():
                    if op == "$lte":
                        if doc.get(key) is None or doc[key] > value:
                            return False
                    elif op == "$gte":
                        if doc.get(key) is None or doc[key] < value:
                            return False
                    else:
                        raise NotImplementedError(op)
            else:
                if doc.get(key) != condition:
                    return False
        return True


@pytest.mark.asyncio
async def test_find_overlapping_detects_real_overlap_with_ongoing_period():
    """既有一筆還在進行中（沒有 end_date）的經期，之後任何新開始日期都算
    重疊——不論新記錄自己有沒有結束日期。"""
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
