from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.prescription import (
    PrescriptionDraft,
    RecognitionResult,
    RecognizedDrug,
)
from app.repositories.prescription_draft_repository import (
    PrescriptionDraftRepository,
)


def _draft(**overrides) -> PrescriptionDraft:
    payload = {
        "draft_id": "D1",
        "creator_user_id": "U_FAMILY",
        "recognition": RecognitionResult(drugs=[RecognizedDrug(name="某藥")]),
        "confidence_level": "medium",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=60),
    }
    payload.update(overrides)
    return PrescriptionDraft(**payload)


def _collection() -> MagicMock:
    collection = MagicMock()
    collection.create_index = AsyncMock()
    collection.insert_one = AsyncMock()
    collection.find_one = AsyncMock(return_value=None)
    collection.find_one_and_update = AsyncMock(return_value=None)
    return collection


@pytest.mark.asyncio
async def test_ensure_indexes_creates_unique_draft_id_and_ttl():
    collection = _collection()

    await PrescriptionDraftRepository.ensure_indexes(collection=collection)

    index_calls = [call.args[0] for call in collection.create_index.call_args_list]
    assert "draft_id" in index_calls

    unique_call = next(
        call
        for call in collection.create_index.call_args_list
        if call.args[0] == "draft_id"
    )
    assert unique_call.kwargs.get("unique") is True

    ttl_call = next(
        call
        for call in collection.create_index.call_args_list
        if call.args[0] == "expires_at"
    )
    assert ttl_call.kwargs.get("expireAfterSeconds") == 0


@pytest.mark.asyncio
async def test_create_persists_the_draft():
    collection = _collection()

    await PrescriptionDraftRepository.create(_draft(), collection=collection)

    (document,), _ = collection.insert_one.call_args
    assert document["draft_id"] == "D1"
    assert document["creator_user_id"] == "U_FAMILY"
    assert document["committed_at"] is None


@pytest.mark.asyncio
async def test_find_filters_by_draft_id_and_creator():
    """草稿只能由建立者讀取。不分別回報「不存在」與「不屬於你」，
    否則就成了確認他人草稿是否存在的探測管道。"""
    collection = _collection()

    await PrescriptionDraftRepository.find_by_id_for_user(
        "D1", "U_FAMILY", collection=collection
    )

    (query,), _ = collection.find_one.call_args
    assert query == {"draft_id": "D1", "creator_user_id": "U_FAMILY"}


@pytest.mark.asyncio
async def test_find_returns_none_when_no_document():
    collection = _collection()

    found = await PrescriptionDraftRepository.find_by_id_for_user(
        "D1", "U_OTHER", collection=collection
    )

    assert found is None


@pytest.mark.asyncio
async def test_find_returns_parsed_draft():
    collection = _collection()
    collection.find_one.return_value = _draft().model_dump(by_alias=True)

    found = await PrescriptionDraftRepository.find_by_id_for_user(
        "D1", "U_FAMILY", collection=collection
    )

    assert found is not None
    assert found.draft_id == "D1"
    assert found.recognition.drugs[0].name == "某藥"


@pytest.mark.asyncio
async def test_mark_committed_requires_uncommitted_draft():
    """提交權以條件式更新取得：同一份草稿被提交兩次時，只有一次能寫入。"""
    collection = _collection()
    collection.find_one_and_update.return_value = {"draft_id": "D1"}

    acquired, medication_ids = await PrescriptionDraftRepository.mark_committed(
        "D1", "U_FAMILY", ["M1", "M2"], collection=collection
    )

    assert acquired is True
    assert medication_ids == ["M1", "M2"]

    (query, update), _ = collection.find_one_and_update.call_args
    assert query["draft_id"] == "D1"
    assert query["creator_user_id"] == "U_FAMILY"
    assert query["committed_at"] is None
    assert update["$set"]["committed_medication_ids"] == ["M1", "M2"]


@pytest.mark.asyncio
async def test_mark_committed_returns_existing_result_when_already_committed():
    collection = _collection()
    collection.find_one_and_update.return_value = None
    collection.find_one.return_value = {"committed_medication_ids": ["M_EXISTING"]}

    acquired, medication_ids = await PrescriptionDraftRepository.mark_committed(
        "D1", "U_FAMILY", ["M_NEW"], collection=collection
    )

    assert acquired is False
    assert medication_ids == ["M_EXISTING"]


@pytest.mark.asyncio
async def test_mark_committed_returns_empty_when_draft_is_gone():
    """TTL 過期後草稿已不存在，不得誤報成「已提交過」而回傳空結果當成功。"""
    collection = _collection()
    collection.find_one_and_update.return_value = None
    collection.find_one.return_value = None

    acquired, medication_ids = await PrescriptionDraftRepository.mark_committed(
        "D1", "U_FAMILY", ["M_NEW"], collection=collection
    )

    assert acquired is False
    assert medication_ids == []
