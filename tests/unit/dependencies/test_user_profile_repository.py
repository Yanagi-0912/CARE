from unittest.mock import AsyncMock, MagicMock
import pytest
from app.repositories.user_profile_repository import UserProfileRepository


# 模擬 update_one 回傳 matched_count > 0（代表更新到既有資料）
@pytest.mark.asyncio
async def test_upsert_user_profile_returns_true_when_document_matched(monkeypatch):

    collection = MagicMock()
    collection.update_one = AsyncMock(
        return_value=MagicMock(matched_count=1, upserted_id=None)
    )
    # setattr底下那一串是import路徑 lambda是去假的資料庫
    monkeypatch.setattr(
        "app.repositories.user_profile_repository.MongoDBManager.get_users_collection",
        lambda: collection,
    )
    # 假裝插入一個user
    ok = await UserProfileRepository.upsert_user_profile("U123", {"name": "Amy"})

    assert ok is True
    collection.update_one.assert_awaited_once()


# 模擬 matched_count = 0 但有 upserted_id（代表新插入成功）
@pytest.mark.asyncio
async def test_upsert_user_profile_returns_true_when_document_inserted(monkeypatch):
    collection = MagicMock()
    collection.update_one = AsyncMock(
        return_value=MagicMock(matched_count=0, upserted_id="new-id")
    )
    monkeypatch.setattr(
        "app.repositories.user_profile_repository.MongoDBManager.get_users_collection",
        lambda: collection,
    )

    ok = await UserProfileRepository.upsert_user_profile("U123", {"name": "Amy"})

    assert ok is True


# matched_count 與 upserted_id 都無效時，應視為未成功寫入
@pytest.mark.asyncio
async def test_upsert_user_profile_returns_false_when_nothing_changed(monkeypatch):

    collection = MagicMock()
    collection.update_one = AsyncMock(
        return_value=MagicMock(matched_count=0, upserted_id=None)
    )
    monkeypatch.setattr(
        "app.repositories.user_profile_repository.MongoDBManager.get_users_collection",
        lambda: collection,
    )

    ok = await UserProfileRepository.upsert_user_profile("U123", {"name": "Amy"})

    assert ok is False


# 驗證 update_one 的 filter / update / upsert 參數結構是否正確
@pytest.mark.asyncio
async def test_upsert_user_profile_builds_expected_update_query(monkeypatch):

    collection = MagicMock()
    collection.update_one = AsyncMock(
        return_value=MagicMock(matched_count=1, upserted_id=None)
    )
    monkeypatch.setattr(
        "app.repositories.user_profile_repository.MongoDBManager.get_users_collection",
        lambda: collection,
    )
    # payload 是要寫入資料庫的內容
    payload = {"name": "Amy", "age": 30}
    await UserProfileRepository.upsert_user_profile("U123", payload)

    args, kwargs = collection.update_one.await_args
    assert args[0] == {"line_id": "U123"}
    assert kwargs["upsert"] is True

    update_doc = args[1]
    assert update_doc["$set"]["line_id"] == "U123"
    assert update_doc["$set"]["name"] == "Amy"
    assert update_doc["$set"]["age"] == 30
    # 時間欄位由 repository 在執行時動態填入
    assert "updated_at" in update_doc["$set"]
    assert "created_at" in update_doc["$setOnInsert"]


# 測試 get_user_profile 能否正確呼叫 find_one 並回傳資料
@pytest.mark.asyncio
async def test_get_user_profile_uses_users_collection_and_returns_document(monkeypatch):

    expected = {"line_id": "U123", "name": "Amy"}
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        "app.repositories.user_profile_repository.MongoDBManager.get_users_collection",
        lambda: collection,
    )

    doc = await UserProfileRepository.get_user_profile("U123")

    assert doc == expected
    collection.find_one.assert_awaited_once_with({"line_id": "U123"})
