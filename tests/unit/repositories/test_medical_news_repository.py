from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.errors import DuplicateKeyError

from app.models.medical_news import DrugNews, make_news_ref
from app.repositories.medical_news_repository import (
    DrugNewsRepository,
    MedicalNewsDeliveryRepository,
    MedicalNewsShareRepository,
    ensure_indexes,
)


def _news(**overrides) -> DrugNews:
    payload = {
        "drug_key": "ACETAMINOPHEN",
        "key_kind": "ingredient",
        "url": "https://www.fda.gov.tw/TC/newsContent.aspx?id=1",
        "title": "某藥品回收公告",
        "source_name": "食藥署",
        "published_at": "2026-08-30",
        "summary": "食藥署公告某批號回收。",
        "concern_kind": "recall",
        "content_hash": "h1",
    }
    payload.update(overrides)
    return DrugNews(**payload)


def _collection(docs=None) -> MagicMock:
    collection = MagicMock()
    collection.create_index = AsyncMock()
    collection.insert_one = AsyncMock()
    collection.update_one = AsyncMock()
    collection.count_documents = AsyncMock(return_value=0)

    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=list(docs or []))
    collection.find = MagicMock(return_value=cursor)
    return collection


# ── 推播權搶佔／去重 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_returns_true_on_first_insert():
    collection = _collection()

    claimed = await MedicalNewsDeliveryRepository.claim(
        "U1", make_news_ref("drug_news", "abc"), 1, collection=collection
    )

    assert claimed is True
    collection.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_claim_returns_false_on_duplicate_key():
    """唯一索引擋下第二次插入時，claim 必須回 False 而不是拋錯。

    這一條同時鎖住去重與多實例搶佔：另一個排程實例先搶到時，本實例必須安靜地
    跳過，而不是讓整個 tick 因例外中斷。
    """
    collection = _collection()
    collection.insert_one = AsyncMock(side_effect=DuplicateKeyError("dup"))

    claimed = await MedicalNewsDeliveryRepository.claim(
        "U1", make_news_ref("drug_news", "abc"), 1, collection=collection
    )

    assert claimed is False


@pytest.mark.asyncio
async def test_claim_does_not_swallow_other_errors():
    """只有 DuplicateKeyError 代表「別人搶到了」。其他錯誤吞掉會讓推播靜默消失。"""
    collection = _collection()
    collection.insert_one = AsyncMock(side_effect=RuntimeError("connection lost"))

    with pytest.raises(RuntimeError):
        await MedicalNewsDeliveryRepository.claim(
            "U1", make_news_ref("drug_news", "abc"), 1, collection=collection
        )


@pytest.mark.asyncio
async def test_share_claim_returns_false_on_duplicate_key():
    """兩位家人分享同一則給同一位收件人，第二次必須被擋下。"""
    collection = _collection()
    collection.insert_one = AsyncMock(side_effect=DuplicateKeyError("dup"))

    claimed = await MedicalNewsShareRepository.claim(
        "U2", make_news_ref("drug_news", "abc"), "U1", collection=collection
    )

    assert claimed is False


# ── 查詢 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_by_drug_keys_filters_by_published_at():
    collection = _collection(docs=[])

    await DrugNewsRepository.find_by_drug_keys(
        ["ACETAMINOPHEN", "IBUPROFEN"], since="2026-08-01", collection=collection
    )

    query = collection.find.call_args.args[0]
    assert query["drug_key"] == {"$in": ["ACETAMINOPHEN", "IBUPROFEN"]}
    assert query["published_at"] == {"$gte": "2026-08-01"}


@pytest.mark.asyncio
async def test_find_by_drug_keys_sorts_by_published_at_desc():
    collection = _collection(docs=[])

    await DrugNewsRepository.find_by_drug_keys(
        ["ACETAMINOPHEN"], since="2026-08-01", collection=collection
    )

    collection.find.return_value.sort.assert_called_once_with("published_at", -1)


@pytest.mark.asyncio
async def test_find_by_drug_keys_returns_empty_without_querying():
    collection = _collection()

    result = await DrugNewsRepository.find_by_drug_keys(
        [], since="2026-08-01", collection=collection
    )

    assert result == []
    collection.find.assert_not_called()


@pytest.mark.asyncio
async def test_find_by_drug_keys_parses_documents():
    doc = _news().model_dump(by_alias=True)
    doc["_id"] = "n1"
    collection = _collection(docs=[doc])

    result = await DrugNewsRepository.find_by_drug_keys(
        ["ACETAMINOPHEN"], since="2026-08-01", collection=collection
    )

    assert len(result) == 1
    assert result[0].id == "n1"
    assert result[0].concern_kind == "recall"


@pytest.mark.asyncio
async def test_upsert_by_url_reports_insert_versus_update():
    collection = _collection()
    collection.update_one = AsyncMock(return_value=MagicMock(upserted_id="n1"))

    created = await DrugNewsRepository.upsert_by_url(_news(), collection=collection)
    assert created is True

    collection.update_one = AsyncMock(return_value=MagicMock(upserted_id=None))
    created_again = await DrugNewsRepository.upsert_by_url(
        _news(), collection=collection
    )
    assert created_again is False


@pytest.mark.asyncio
async def test_upsert_by_url_keys_on_url():
    collection = _collection()
    collection.update_one = AsyncMock(return_value=MagicMock(upserted_id="n1"))

    news = _news()
    await DrugNewsRepository.upsert_by_url(news, collection=collection)

    filter_query = collection.update_one.call_args.args[0]
    assert filter_query == {"url": news.url}


@pytest.mark.asyncio
async def test_list_pushed_refs_returns_set_of_refs():
    since = datetime.now(timezone.utc) - timedelta(days=30)
    collection = _collection(
        docs=[{"news_ref": "drug_news:aaa"}, {"news_ref": "kb_article:bbb"}]
    )

    refs = await MedicalNewsDeliveryRepository.list_pushed_refs(
        "U1", since=since, collection=collection
    )

    assert refs == {"drug_news:aaa", "kb_article:bbb"}


@pytest.mark.asyncio
async def test_mark_shared_records_recipient_count():
    collection = _collection()
    ref = make_news_ref("drug_news", "abc")

    await MedicalNewsDeliveryRepository.mark_shared(
        "U1", ref, recipient_count=3, collection=collection
    )

    filter_query, update = collection.update_one.call_args.args[:2]
    assert filter_query == {"user_id": "U1", "news_ref": ref}
    assert update["$set"]["share_recipient_count"] == 3
    assert "shared_at" in update["$set"]


@pytest.mark.asyncio
async def test_count_shares_today_filters_by_day_start():
    day_start = datetime(2026, 9, 2, tzinfo=timezone.utc)
    collection = _collection()
    collection.count_documents = AsyncMock(return_value=2)

    count = await MedicalNewsDeliveryRepository.count_shares_today(
        "U1", day_start=day_start, collection=collection
    )

    assert count == 2
    query = collection.count_documents.call_args.args[0]
    assert query["user_id"] == "U1"
    assert query["shared_at"] == {"$gte": day_start}


# ── 索引 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_indexes_creates_all_unique_constraints():
    drug_news = _collection()
    deliveries = _collection()
    shares = _collection()

    await ensure_indexes(
        drug_news_collection=drug_news,
        deliveries_collection=deliveries,
        shares_collection=shares,
    )

    delivery_call = deliveries.create_index.call_args_list[0]
    assert delivery_call.args[0] == [("user_id", 1), ("news_ref", 1)]
    assert delivery_call.kwargs.get("unique") is True

    share_call = shares.create_index.call_args_list[0]
    assert share_call.args[0] == [("recipient_id", 1), ("news_ref", 1)]
    assert share_call.kwargs.get("unique") is True

    url_call = next(
        call
        for call in drug_news.create_index.call_args_list
        if call.args[0] == "url"
    )
    assert url_call.kwargs.get("unique") is True


@pytest.mark.asyncio
async def test_ensure_indexes_survives_index_creation_failure():
    """既有資料有重複時建索引會失敗，但不該讓 app 起不來。

    比照 MedicationLogRepository.ensure_indexes 的既有處理：吞掉例外但留下
    exception log——沒有唯一索引時去重與搶佔都會失效，維運必須看得到。
    """
    drug_news = _collection()
    deliveries = _collection()
    deliveries.create_index = AsyncMock(side_effect=RuntimeError("duplicate data"))
    shares = _collection()

    await ensure_indexes(
        drug_news_collection=drug_news,
        deliveries_collection=deliveries,
        shares_collection=shares,
    )

    shares.create_index.assert_awaited()
