"""MedicalFacilityRepository 查哪一個 collection。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.mongodb import MongoDBManager
from app.repositories.medical_facility_repository import MedicalFacilityRepository
from bson import ObjectId


class _Cursor:
    def limit(self, _):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def _collection() -> MagicMock:
    collection = MagicMock()
    collection.find.return_value = _Cursor()
    return collection


@pytest.mark.asyncio
async def test_default_repository_reads_medical_facilities(monkeypatch):
    # 先建 repository 再換掉 getter：collection 必須在查詢當下才取，
    # service 單例在 MongoDB 設定好之前就已經建立。
    repository = MedicalFacilityRepository()
    facilities = _collection()
    pharmacies = _collection()
    monkeypatch.setattr(
        MongoDBManager, "get_medical_collection", classmethod(lambda cls: facilities)
    )
    monkeypatch.setattr(
        MongoDBManager, "get_pharmacy_collection", classmethod(lambda cls: pharmacies)
    )

    await repository.find_by_query({"name": "x"}, 5)

    facilities.find.assert_called_once_with({"name": "x"})
    pharmacies.find.assert_not_called()


@pytest.mark.asyncio
async def test_repository_with_getter_reads_that_collection(monkeypatch):
    facilities = _collection()
    pharmacies = _collection()
    monkeypatch.setattr(
        MongoDBManager, "get_medical_collection", classmethod(lambda cls: facilities)
    )
    repository = MedicalFacilityRepository(lambda: pharmacies)

    await repository.find_by_query({"name": "x"}, 5)

    pharmacies.find.assert_called_once_with({"name": "x"})
    facilities.find.assert_not_called()


class _Documents(_Cursor):
    def __init__(self, documents):
        self.documents = iter(documents)

    async def __anext__(self):
        try:
            return next(self.documents)
        except StopIteration:
            raise StopAsyncIteration


def _document(name, distance):
    return {"_id":ObjectId(), "name":name, "type":"藥妝門市", "distance_calculated":distance}


@pytest.mark.asyncio
async def test_name_search_merges_pharmacies_and_retail_by_distance():
    pharmacies, retail = _collection(), _collection()
    pharmacies.aggregate.return_value = _Documents([_document("康是美北峰藥局", 13000)])
    retail.aggregate.return_value = _Documents([_document("康是美廟口門市", 400)])
    repository = MedicalFacilityRepository(lambda: pharmacies, supplemental_collection_getter=lambda: retail)

    results = await repository.find_by_query_near({"name":{"$regex":"康是美"}}, 25.1, 121.7, 1)

    assert [item.name for item in results] == ["康是美廟口門市"]


@pytest.mark.asyncio
async def test_nearby_pharmacy_never_returns_general_retail():
    pharmacies, retail = _collection(), _collection()
    pharmacies.aggregate.return_value = _Documents([])
    repository = MedicalFacilityRepository(lambda: pharmacies, supplemental_collection_getter=lambda: retail)

    await repository.find_near(25.1, 121.7, 5000, 5)

    pharmacies.aggregate.assert_called_once()
    retail.aggregate.assert_not_called()


@pytest.mark.asyncio
async def test_retail_card_id_can_be_opened():
    pharmacies, retail = _collection(), _collection()
    doc = _document("康是美廟口門市", 400)
    pharmacies.find_one = AsyncMock(return_value=None)
    retail.find_one = AsyncMock(return_value=doc)
    repository = MedicalFacilityRepository(lambda: pharmacies, supplemental_collection_getter=lambda: retail)

    result = await repository.find_by_id(str(doc["_id"]))

    assert result.name == "康是美廟口門市"


def test_original_registered_name_remains_searchable_after_brand_name_is_added():
    repository = MedicalFacilityRepository(lambda: None, supplemental_collection_getter=lambda: None)
    query = {"name":{"$regex":"大里大樹"},"address":{"$regex":"臺中"}}
    expanded = repository._with_name_aliases(query)

    assert expanded == {"$or":[query,{"aliases":{"$regex":"大里大樹"},"address":{"$regex":"臺中"}}]}
    assert query == {"name":{"$regex":"大里大樹"},"address":{"$regex":"臺中"}}
