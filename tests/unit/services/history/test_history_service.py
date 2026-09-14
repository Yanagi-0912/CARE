"""每一輪對話同時寫進正式紀錄（Mongo）與快取（Redis）。"""

from datetime import datetime, timezone

from app.models.chat_message import ChatMessage
from app.services.history.history_service import LineMessageHistoryService


class FakeCache:
    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    async def append_message(self, line_id: str, message: ChatMessage) -> None:
        self.messages.append(message)

    async def list_messages(self, line_id: str) -> list[ChatMessage]:
        return list(self.messages)


class FakeConversationLog:
    def __init__(self, error: Exception | None = None) -> None:
        self.messages: list[ChatMessage] = []
        self._error = error

    async def append_message(self, line_id: str, message: ChatMessage) -> None:
        if self._error is not None:
            raise self._error
        self.messages.append(message)


async def test_turn_is_recorded_in_conversation_log():
    log = FakeConversationLog()
    service = LineMessageHistoryService(FakeCache(), conversation_log=log)

    await service.save_turn(
        "U123", "今天頭痛", "建議多休息", "text", datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    )

    assert [(m.message_type, m.content) for m in log.messages] == [
        ("text", "今天頭痛"),
        ("assistant_reply", "建議多休息"),
    ]


# 寫紀錄發生在回覆送出之後；Mongo 出錯不能讓這一輪變成錯誤，
# 也不能拖累 agent 下一輪要讀的 Redis 快取。
async def test_conversation_log_failure_keeps_cache_and_does_not_raise():
    cache = FakeCache()
    service = LineMessageHistoryService(
        cache, conversation_log=FakeConversationLog(error=RuntimeError("mongo down"))
    )

    await service.save_turn(
        "U123", "今天頭痛", "建議多休息", "text", datetime(2026, 9, 14, 2, 0, tzinfo=timezone.utc)
    )

    assert [m.content for m in cache.messages] == ["今天頭痛", "建議多休息"]
