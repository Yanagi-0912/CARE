"""Redis 只當 agent 讀最近幾則的快取，不再無上限累積。"""

from datetime import datetime, timezone

from app.models.chat_message import ChatMessage
from app.repositories.chat_history_repository import RedisChatHistoryRepository


class FakeRedis:
    """只實作用到的指令，語意照 Redis list（負索引從尾端算）。"""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        items = self.lists.get(key, [])
        stop = None if end == -1 else end + 1
        self.lists[key] = items[start:stop]
        return True

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self.lists.get(key, [])
        stop = None if end == -1 else end + 1
        return items[start:stop]


async def test_cache_keeps_only_the_most_recent_messages():
    repository = RedisChatHistoryRepository(FakeRedis(), max_messages=5)
    for i in range(7):
        await repository.append_message(
            "U123",
            ChatMessage(
                line_id="U123",
                message_type="text",
                content=f"msg_{i}",
                timestamp=datetime(2026, 9, 14, 2, i, tzinfo=timezone.utc),
            ),
        )

    messages = await repository.list_messages("U123")

    assert [m.content for m in messages] == ["msg_2", "msg_3", "msg_4", "msg_5", "msg_6"]
