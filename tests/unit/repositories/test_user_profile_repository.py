from unittest.mock import AsyncMock, MagicMock
import pytest
from app.repositories.user_profile_repository import UserProfileRepository


# fixture用來在測試函數中提供一些預先設定好的物件或狀態，讓測試更簡潔和可重複使用
# setattr:當 user_profile_repository.py 裡呼叫 MongoDBManager.get_users_collection() 時，
# 不要跑真實的，改回傳我的 fake collection。
@pytest.fixture()
def override_users_collection(monkeypatch):
    def _override(collection):
        monkeypatch.setattr(
            "app.repositories.user_profile_repository.MongoDBManager.get_users_collection",
            lambda: collection,
        )
        return collection

    return _override


# 模擬 update_one 回傳 matched_count > 0（代表更新到既有資料）
@pytest.mark.asyncio
async def test_upsert_user_profile_returns_true_when_document_matched(
    override_users_collection,
):
    collection = MagicMock()
    collection.update_one = AsyncMock(
        return_value=MagicMock(matched_count=1, upserted_id=None)
    )
    # 呼叫它，把 fake collection 傳進去，觸發 setattr 替換
    override_users_collection(collection)

    ok = await UserProfileRepository.upsert_user_profile("U123", {"name": "Amy"})

    assert ok is True
    collection.update_one.assert_awaited_once()


# 模擬 matched_count = 0 但有 upserted_id（代表新插入成功）
@pytest.mark.asyncio
async def test_upsert_user_profile_returns_true_when_document_inserted(
    override_users_collection,
):
    collection = MagicMock()
    collection.update_one = AsyncMock(
        return_value=MagicMock(matched_count=0, upserted_id="new-id")
    )
    override_users_collection(collection)

    ok = await UserProfileRepository.upsert_user_profile("U123", {"name": "Amy"})

    assert ok is True


# matched_count 與 upserted_id 都無效時，應視為未成功寫入
@pytest.mark.asyncio
async def test_upsert_user_profile_returns_false_when_nothing_changed(
    override_users_collection,
):
    collection = MagicMock()
    collection.update_one = AsyncMock(
        return_value=MagicMock(matched_count=0, upserted_id=None)
    )
    override_users_collection(collection)

    ok = await UserProfileRepository.upsert_user_profile("U123", {"name": "Amy"})

    assert ok is False


# 驗證 update_one 的 filter / update / upsert 參數結構是否正確
@pytest.mark.asyncio
async def test_upsert_user_profile_builds_expected_update_query(
    override_users_collection,
):
    collection = MagicMock()
    collection.update_one = AsyncMock(
        return_value=MagicMock(matched_count=1, upserted_id=None)
    )
    override_users_collection(collection)

    payload = {"name": "Amy", "age": 30}
    await UserProfileRepository.upsert_user_profile("U123", payload)

    args, kwargs = collection.update_one.await_args
    assert args[0] == {"line_id": "U123"}
    assert kwargs["upsert"] is True

    update_doc = args[1]
    assert update_doc["$set"]["line_id"] == "U123"
    assert update_doc["$set"]["name"] == "Amy"
    assert update_doc["$set"]["age"] == 30
    assert "updated_at" in update_doc["$set"]
    assert "created_at" in update_doc["$setOnInsert"]


@pytest.mark.asyncio
async def test_sync_line_profile_updates_only_line_fields(override_users_collection):
    collection = MagicMock()
    collection.update_one = AsyncMock(
        return_value=MagicMock(matched_count=1, upserted_id=None)
    )
    override_users_collection(collection)

    ok = await UserProfileRepository.sync_line_profile(
        "U123",
        picture_url="https://line.example/pic.jpg",
    )

    assert ok is True
    args, kwargs = collection.update_one.await_args
    assert args[0] == {"line_id": "U123"}
    assert kwargs.get("upsert") is not True
    update_doc = args[1]["$set"]
    assert update_doc["picture_url"] == "https://line.example/pic.jpg"
    assert "updated_at" in update_doc
    assert "name" not in update_doc


@pytest.mark.asyncio
async def test_sync_line_profile_returns_false_when_no_fields_provided(
    override_users_collection,
):
    collection = MagicMock()
    override_users_collection(collection)

    ok = await UserProfileRepository.sync_line_profile("U123")

    assert ok is False
    collection.update_one.assert_not_called()


@pytest.mark.asyncio
async def test_update_user_settings_uses_dot_notation_for_partial_fields(
    override_users_collection,
):
    collection = MagicMock()
    collection.update_one = AsyncMock(
        return_value=MagicMock(matched_count=1, upserted_id=None)
    )
    override_users_collection(collection)

    ok = await UserProfileRepository.update_user_settings(
        "U123", {"font_size": "xlarge", "high_contrast": False}
    )

    assert ok is True
    args, kwargs = collection.update_one.await_args
    assert args[0] == {"line_id": "U123"}
    assert kwargs.get("upsert") is not True

    update_doc = args[1]["$set"]
    assert update_doc["settings.font_size"] == "xlarge"
    assert update_doc["settings.high_contrast"] is False
    assert "updated_at" in update_doc
    # 沒帶到的欄位不應該出現在更新內容裡
    assert "settings.notify_reminder" not in update_doc


@pytest.mark.asyncio
async def test_update_user_settings_returns_false_when_no_fields_provided(
    override_users_collection,
):
    collection = MagicMock()
    override_users_collection(collection)

    ok = await UserProfileRepository.update_user_settings("U123", {})

    assert ok is False
    collection.update_one.assert_not_called()


@pytest.mark.asyncio
async def test_update_user_settings_returns_false_when_no_document_matched(
    override_users_collection,
):
    collection = MagicMock()
    collection.update_one = AsyncMock(
        return_value=MagicMock(matched_count=0, upserted_id=None)
    )
    override_users_collection(collection)

    ok = await UserProfileRepository.update_user_settings(
        "U123", {"font_size": "normal"}
    )

    assert ok is False


# 測試 get_user_profile 能否正確呼叫 find_one 並回傳資料
@pytest.mark.asyncio
async def test_get_user_profile_uses_users_collection_and_returns_document(
    override_users_collection,
):
    expected = {"line_id": "U123", "name": "Amy"}
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=expected)
    override_users_collection(collection)

    doc = await UserProfileRepository.get_user_profile("U123")

    assert doc == expected
    collection.find_one.assert_awaited_once_with({"line_id": "U123"})


@pytest.mark.asyncio
async def test_update_voice_reply_enabled_only_sets_voice_field(
    override_users_collection,
):
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    override_users_collection(collection)

    ok = await UserProfileRepository.update_voice_reply_enabled("U123", False)

    assert ok is True
    collection.update_one.assert_awaited_once()
    args, kwargs = collection.update_one.await_args
    assert args[0] == {"line_id": "U123"}
    assert args[1]["$set"]["voice_reply_enabled"] is False
    assert "updated_at" in args[1]["$set"]
    assert kwargs == {}
