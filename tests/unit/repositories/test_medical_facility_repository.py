"""MedicalFacilityRepository 查哪一個 collection。"""

from unittest.mock import MagicMock

import pytest

from app.db.mongodb import MongoDBManager
from app.repositories.medical_facility_repository import MedicalFacilityRepository


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
