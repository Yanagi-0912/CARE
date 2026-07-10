from unittest.mock import MagicMock

import pytest

from app.db.mongodb import MongoDBManager


@pytest.fixture(autouse=True)
def reset_manager_state():
    """避免測試之間互相汙染 class 層級的 _client 快取。"""
    original_client = MongoDBManager._client
    original_url = MongoDBManager._mongodb_url
    yield
    MongoDBManager._client = original_client
    MongoDBManager._mongodb_url = original_url


def test_get_database_uses_configured_mongodb_db(monkeypatch):
    fake_client = MagicMock()
    monkeypatch.setattr(MongoDBManager, "get_client", classmethod(lambda cls: fake_client))
    monkeypatch.setattr("app.db.mongodb.settings.MONGODB_DB", "some_other_db")

    MongoDBManager.get_database()

    fake_client.__getitem__.assert_called_once_with("some_other_db")


def test_get_database_raises_when_mongodb_db_not_configured(monkeypatch):
    monkeypatch.setattr(MongoDBManager, "get_client", classmethod(lambda cls: MagicMock()))
    monkeypatch.setattr("app.db.mongodb.settings.MONGODB_DB", "")

    with pytest.raises(ValueError, match="MONGODB_DB"):
        MongoDBManager.get_database()


def test_collection_getters_use_configured_database_name(monkeypatch):
    fake_db = MagicMock()
    monkeypatch.setattr(MongoDBManager, "get_database", classmethod(lambda cls: fake_db))

    MongoDBManager.get_users_collection()
    MongoDBManager.get_medical_collection()
    MongoDBManager.get_family_tree_collection()
    MongoDBManager.get_pending_invitations_collection()
    MongoDBManager.get_consultation_summaries_collection()

    fake_db.__getitem__.assert_any_call("users")
    fake_db.__getitem__.assert_any_call("medicalFacilities")
    fake_db.__getitem__.assert_any_call("family_trees")
    fake_db.__getitem__.assert_any_call("pending_invitations")
    fake_db.__getitem__.assert_any_call("consultation_summaries")


def test_get_client_raises_when_url_not_configured(monkeypatch):
    monkeypatch.setattr(MongoDBManager, "_client", None)
    monkeypatch.setattr(MongoDBManager, "_mongodb_url", "")

    with pytest.raises(ValueError, match="MongoDB_url"):
        MongoDBManager.get_client()
