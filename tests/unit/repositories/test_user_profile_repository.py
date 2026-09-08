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
