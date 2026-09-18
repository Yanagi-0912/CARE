from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.step_session_repository import StepSessionRepository


def _collection() -> MagicMock:
    collection = MagicMock()
    collection.create_index = AsyncMock()
    collection.update_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=None)
    return collection


@pytest.mark.asyncio
async def test_ensure_indexes_creates_unique_session_index_and_date_index():
    collection = _collection()

    await StepSessionRepository.ensure_indexes(collection=collection)

    unique_call = next(
        call
        for call in collection.create_index.call_args_list
        if call.args[0] == [("user_id", 1), ("session_id", 1)]
    )
    assert unique_call.kwargs.get("unique") is True

    date_call = next(
        call
        for call in collection.create_index.call_args_list
        if call.args[0] == [("user_id", 1), ("date", 1)]
    )
    assert "unique" not in date_call.kwargs or not date_call.kwargs["unique"]


@pytest.mark.asyncio
async def test_sync_progress_upserts_with_max_steps():
    collection = _collection()
    started_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    collection.find_one = AsyncMock(
        return_value={
            "user_id": "U1",
            "session_id": "S1",
            "date": "2026-09-01",
            "steps": 300,
            "started_at": started_at,
            "last_synced_at": started_at,
        }
    )

    session = await StepSessionRepository.sync_progress(
        user_id="U1",
        session_id="S1",
        date_str="2026-09-01",
        steps=300,
        started_at=started_at,
        collection=collection,
    )

    assert session.steps == 300
    collection.update_one.assert_awaited_once()
    (query, update), kwargs = collection.update_one.call_args
    assert query == {"user_id": "U1", "session_id": "S1"}
    assert update["$max"] == {"steps": 300}
    assert "last_synced_at" in update["$set"]
    assert update["$setOnInsert"]["date"] == "2026-09-01"
    assert update["$setOnInsert"]["started_at"] == started_at
    assert kwargs.get("upsert") is True


class _FakeStepSessionCollection:
    """支援 $max／$set／$setOnInsert／upsert 的極簡假集合，用來證明冪等性
    行為真的成立（重送、亂序、$max 語意），而不只是驗證查詢字典的長相。"""

    def __init__(self):
        self.docs: dict[tuple, dict] = {}

    async def update_one(self, query, update, upsert=False):
        key = (query["user_id"], query["session_id"])
        doc = self.docs.get(key)
        if doc is None:
            if not upsert:
                return MagicMock(matched_count=0)
            doc = dict(update.get("$setOnInsert", {}))
            self.docs[key] = doc
        if "$max" in update:
            for field, value in update["$max"].items():
                doc[field] = max(doc.get(field, value), value)
        if "$set" in update:
            doc.update(update["$set"])
        return MagicMock(matched_count=1)

    async def find_one(self, query):
        key = (query["user_id"], query["session_id"])
        return self.docs.get(key)

    def aggregate(self, pipeline):
        """極簡管線解讀，支援兩種形狀：``get_daily_total`` 的單日加總
        （``$match`` 精確比對、``$group`` 依 ``None`` 分組）與
        ``list_daily_totals`` 的區間查詢（``$match`` 支援 ``$gte``／``$lte``
        範圍、``$group`` 依 ``$date`` 分組、``$sort`` 排序）。"""

        def _matches(doc: dict) -> bool:
            for key, condition in pipeline[0]["$match"].items():
                value = doc.get(key)
                if isinstance(condition, dict):
                    if "$gte" in condition and value < condition["$gte"]:
                        return False
                    if "$lte" in condition and value > condition["$lte"]:
                        return False
                elif value != condition:
                    return False
            return True

        matched = [doc for doc in self.docs.values() if _matches(doc)]

        group_key = pipeline[1]["$group"]["_id"]
        if group_key is None:
            results = (
                [{"_id": None, "total": sum(doc.get("steps", 0) for doc in matched)}]
                if matched
                else []
            )
        else:
            field = str(group_key).lstrip("$")
            totals: dict = {}
            for doc in matched:
                key = doc.get(field)
                totals[key] = totals.get(key, 0) + doc.get("steps", 0)
            results = [{"_id": key, "total": total} for key, total in totals.items()]

        if len(pipeline) > 2 and "$sort" in pipeline[2]:
            for field, direction in reversed(list(pipeline[2]["$sort"].items())):
                results.sort(key=lambda r: r[field], reverse=direction < 0)

        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=results)
        return cursor


@pytest.mark.asyncio
async def test_resending_the_same_cumulative_value_is_idempotent():
    """step-counter spec「重送同一個值」：同一工作階段的累計 300 步被送達
    兩次，該工作階段的步數 SHALL 為 300。"""
    collection = _FakeStepSessionCollection()
    started_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

    await StepSessionRepository.sync_progress(
        "U1", "S1", "2026-09-01", 300, started_at, collection=collection
    )
    session = await StepSessionRepository.sync_progress(
        "U1", "S1", "2026-09-01", 300, started_at, collection=collection
    )

    assert session.steps == 300


@pytest.mark.asyncio
async def test_out_of_order_smaller_value_does_not_decrease_steps():
    """step-counter spec「亂序送達」：先收到 120，再收到較早送出的 100，
    該工作階段的步數 SHALL 維持 120。"""
    collection = _FakeStepSessionCollection()
    started_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

    await StepSessionRepository.sync_progress(
        "U1", "S1", "2026-09-01", 120, started_at, collection=collection
    )
    session = await StepSessionRepository.sync_progress(
        "U1", "S1", "2026-09-01", 100, started_at, collection=collection
    )

    assert session.steps == 120


@pytest.mark.asyncio
async def test_daily_total_sums_multiple_sessions_on_the_same_day():
    """step-counter spec「同一天兩個工作階段」：累計分別為 300 與 500，
    當日步數 SHALL 為 800。"""
    collection = _FakeStepSessionCollection()
    started_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

    await StepSessionRepository.sync_progress(
        "U1", "S1", "2026-09-01", 300, started_at, collection=collection
    )
    await StepSessionRepository.sync_progress(
        "U1", "S2", "2026-09-01", 500, started_at, collection=collection
    )

    total = await StepSessionRepository.get_daily_total(
        "U1", "2026-09-01", collection=collection
    )

    assert total.steps == 800
    assert total.user_id == "U1"
    assert total.date == "2026-09-01"


@pytest.mark.asyncio
async def test_daily_total_does_not_include_other_dates_or_users():
    collection = _FakeStepSessionCollection()
    started_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

    await StepSessionRepository.sync_progress(
        "U1", "S1", "2026-09-01", 300, started_at, collection=collection
    )
    await StepSessionRepository.sync_progress(
        "U1", "S2", "2026-09-02", 999, started_at, collection=collection
    )
    await StepSessionRepository.sync_progress(
        "U2", "S3", "2026-09-01", 999, started_at, collection=collection
    )

    total = await StepSessionRepository.get_daily_total(
        "U1", "2026-09-01", collection=collection
    )

    assert total.steps == 300


@pytest.mark.asyncio
async def test_daily_total_is_zero_when_no_sessions():
    collection = _FakeStepSessionCollection()

    total = await StepSessionRepository.get_daily_total(
        "U1", "2026-09-01", collection=collection
    )

    assert total.steps == 0


# ── get_session：讀取既有工作階段（供服務層合理性檢查用，不寫入）───────


@pytest.mark.asyncio
async def test_get_session_returns_none_when_session_does_not_exist():
    collection = _FakeStepSessionCollection()

    session = await StepSessionRepository.get_session(
        "U1", "S1", collection=collection
    )

    assert session is None


@pytest.mark.asyncio
async def test_get_session_returns_the_stored_session():
    collection = _FakeStepSessionCollection()
    started_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    await StepSessionRepository.sync_progress(
        "U1", "S1", "2026-09-01", 300, started_at, collection=collection
    )

    session = await StepSessionRepository.get_session(
        "U1", "S1", collection=collection
    )

    assert session is not None
    assert session.steps == 300
    assert session.started_at == started_at
    assert session.date == "2026-09-01"


# ── list_daily_totals：GET /api/health/steps 的區間查詢 ─────────────────


@pytest.mark.asyncio
async def test_list_daily_totals_omits_dates_without_sessions_and_sorts_newest_first():
    collection = _FakeStepSessionCollection()
    started_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    await StepSessionRepository.sync_progress(
        "U1", "S1", "2026-09-01", 300, started_at, collection=collection
    )
    await StepSessionRepository.sync_progress(
        "U1", "S2", "2026-09-03", 500, started_at, collection=collection
    )

    totals = await StepSessionRepository.list_daily_totals(
        "U1", "2026-09-01", "2026-09-07", collection=collection
    )

    assert [(t.date, t.steps) for t in totals] == [
        ("2026-09-03", 500),
        ("2026-09-01", 300),
    ]


@pytest.mark.asyncio
async def test_list_daily_totals_sums_multiple_sessions_on_the_same_date():
    collection = _FakeStepSessionCollection()
    started_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    await StepSessionRepository.sync_progress(
        "U1", "S1", "2026-09-01", 300, started_at, collection=collection
    )
    await StepSessionRepository.sync_progress(
        "U1", "S2", "2026-09-01", 500, started_at, collection=collection
    )

    totals = await StepSessionRepository.list_daily_totals(
        "U1", "2026-09-01", "2026-09-01", collection=collection
    )

    assert [(t.date, t.steps) for t in totals] == [("2026-09-01", 800)]


@pytest.mark.asyncio
async def test_list_daily_totals_excludes_dates_outside_the_range():
    collection = _FakeStepSessionCollection()
    started_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    await StepSessionRepository.sync_progress(
        "U1", "S1", "2026-08-31", 999, started_at, collection=collection
    )
    await StepSessionRepository.sync_progress(
        "U1", "S2", "2026-09-08", 999, started_at, collection=collection
    )

    totals = await StepSessionRepository.list_daily_totals(
        "U1", "2026-09-01", "2026-09-07", collection=collection
    )

    assert totals == []


@pytest.mark.asyncio
async def test_list_daily_totals_excludes_other_users():
    collection = _FakeStepSessionCollection()
    started_at = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    await StepSessionRepository.sync_progress(
        "U2", "S1", "2026-09-01", 999, started_at, collection=collection
    )

    totals = await StepSessionRepository.list_daily_totals(
        "U1", "2026-09-01", "2026-09-07", collection=collection
    )

    assert totals == []
