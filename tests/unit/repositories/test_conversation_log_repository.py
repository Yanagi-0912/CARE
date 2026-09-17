"""對話原文的正式紀錄（Mongo）。

Redis 只剩 agent 讀最近幾則的快取；摘要、原始訊息查詢都以這份為準，
所以寫入格式、保存期限、讀回的時區都是對外契約。
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from app.models.chat_message import ChatMessage
from app.repositories.conversation_log_repository import ConversationLogRepository


def _message(timestamp: datetime) -> ChatMessage:
    return ChatMessage(
        line_id="U123", message_type="text", content="今天頭痛", timestamp=timestamp
    )


# 保存政策（2026-09-14 決定）：原文保留 30 天，由 Mongo TTL 依 expires_at 清除。
async def test_message_expires_thirty_days_after_it_was_sent():
    collection = MagicMock()
    collection.insert_one = AsyncMock()

    await ConversationLogRepository.append_message(
        "U123",
        _message(datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)),
        collection=collection,
    )

    [document] = collection.insert_one.await_args.args
    assert document["line_id"] == "U123"
    assert document["content"] == "今天頭痛"
    assert document["expires_at"] == datetime(2026, 10, 14, 2, 0, tzinfo=timezone.utc)


# Motor 未開 tz_aware，讀回的是 naive UTC。直接交出去的話，LIFF 會把沒有時區的
# ISO 字串當成手機本地時間，顯示時間差 8 小時。
async def test_listed_messages_carry_utc_timezone():
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(
        return_value=[
            {
                "line_id": "U123",
                "message_type": "text",
                "content": "今天頭痛",
                "timestamp": datetime(2026, 9, 14, 2, 0),
            }
        ]
    )
    collection = MagicMock()
    collection.find.return_value = cursor

    [message] = await ConversationLogRepository.list_messages(
        "U123", collection=collection
    )

    assert message.timestamp == datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    assert collection.find.call_args.args[0] == {"line_id": "U123"}
    cursor.sort.assert_called_once_with("timestamp", 1)


async def test_retention_is_enforced_by_ttl_index_on_expires_at():
    collection = MagicMock()
    collection.create_index = AsyncMock()

    await ConversationLogRepository.ensure_indexes(collection=collection)

    ttl_calls = [
        call
        for call in collection.create_index.await_args_list
        if call.kwargs.get("expireAfterSeconds") is not None
    ]
    assert [(call.args[0], call.kwargs["expireAfterSeconds"]) for call in ttl_calls] == [
        ([("expires_at", 1)], 0)
    ]


# 每日摘要排程只需要昨天那一天：查詢要限定在區間內，不是撈 30 天再在 Python 過濾。
async def test_list_messages_limits_query_to_utc_range():
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[])
    collection = MagicMock()
    collection.find.return_value = cursor
    since = datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
    until = datetime(2026, 9, 14, 16, 0, tzinfo=timezone.utc)

    await ConversationLogRepository.list_messages(
        "U123", collection=collection, since=since, until=until
    )

    query, projection = collection.find.call_args.args
    assert query == {"line_id": "U123", "timestamp": {"$gte": since, "$lt": until}}
    assert projection == {"_id": 0, "expires_at": 0}


async def test_list_messages_without_range_queries_everything():
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[])
    collection = MagicMock()
    collection.find.return_value = cursor

    await ConversationLogRepository.list_messages("U123", collection=collection)

    assert collection.find.call_args.args[0] == {"line_id": "U123"}


async def test_list_line_ids_with_range_only_counts_users_active_in_range():
    collection = MagicMock()
    collection.distinct = AsyncMock(return_value=["U2", "U1"])
    since = datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
    until = datetime(2026, 9, 14, 16, 0, tzinfo=timezone.utc)

    ids = await ConversationLogRepository.list_line_ids(
        collection=collection, since=since, until=until
    )

    assert ids == ["U1", "U2"]
    collection.distinct.assert_awaited_once_with(
        "line_id", {"timestamp": {"$gte": since, "$lt": until}}
    )


async def test_list_line_ids_without_range_has_empty_filter():
    collection = MagicMock()
    collection.distinct = AsyncMock(return_value=[])

    await ConversationLogRepository.list_line_ids(collection=collection)

    collection.distinct.assert_awaited_once_with("line_id", {})
