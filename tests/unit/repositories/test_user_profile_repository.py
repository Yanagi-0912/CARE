from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.user_profile_repository import UserProfileRepository


def _collection(docs=None) -> MagicMock:
    collection = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=list(docs or []))
    collection.find = MagicMock(return_value=cursor)
    return collection


@pytest.mark.asyncio
async def test_list_all_line_ids_returns_every_user():
    """每日消息卡的收件人是全體使用者，不是「有用藥的那批」。

    Tier 2 保底存在的理由就是讓沒有用藥資料的人也收得到東西；若這裡改成只回
    有用藥的使用者，Tier 2 等於永遠不會送給它真正的目標對象。
    """
    collection = _collection(docs=[{"line_id": "U1"}, {"line_id": "U2"}])

    ids = await UserProfileRepository.list_all_line_ids(collection=collection)

    assert ids == ["U1", "U2"]


@pytest.mark.asyncio
async def test_list_all_line_ids_skips_documents_without_line_id():
    collection = _collection(docs=[{"line_id": "U1"}, {}, {"line_id": ""}])

    ids = await UserProfileRepository.list_all_line_ids(collection=collection)

    assert ids == ["U1"]


@pytest.mark.asyncio
async def test_list_all_line_ids_projects_only_line_id():
    """使用者文件含健康資料，全欄位撈回來只為了取一個 id 是不必要的暴露。"""
    collection = _collection(docs=[])

    await UserProfileRepository.list_all_line_ids(collection=collection)

    projection = collection.find.call_args.args[1]
    assert projection == {"line_id": 1}


@pytest.mark.asyncio
async def test_list_all_line_ids_returns_each_id_once():
    """唯一索引建起來之前資料庫可能有同一個 line_id 的多份文件；回傳重複的話
    同一位使用者會在同一輪被推播兩次。保留首次出現的順序。"""
    collection = _collection(
        docs=[{"line_id": "U1"}, {"line_id": "U2"}, {"line_id": "U1"}, {"line_id": "U2"}]
    )

    ids = await UserProfileRepository.list_all_line_ids(collection=collection)

    assert ids == ["U1", "U2"]


@pytest.mark.asyncio
async def test_ensure_indexes_creates_unique_line_id_index():
    """沒有唯一索引，首次登入的併發 upsert 會生出同一個人的多份文件。"""
    collection = MagicMock()
    collection.create_index = AsyncMock()

    await UserProfileRepository.ensure_indexes(collection=collection)

    collection.create_index.assert_awaited_once_with(
        [("line_id", 1)], unique=True, name="users_line_id_unique"
    )


@pytest.mark.asyncio
async def test_ensure_indexes_propagates_duplicate_key_error():
    """既有重複資料時要讓呼叫端（lifespan）看得到例外並留 log，這裡不吞。"""
    from pymongo.errors import DuplicateKeyError

    collection = MagicMock()
    collection.create_index = AsyncMock(side_effect=DuplicateKeyError("dup"))

    with pytest.raises(DuplicateKeyError):
        await UserProfileRepository.ensure_indexes(collection=collection)
