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


# --- Flex JSON 與保底句不進歷史（2026-09-16） ----------------------------------

import json

from app.i18n.messages import t
from app.services.history.history_service import flex_history_placeholder

_WHEN = datetime(2026, 9, 16, 2, 0, tzinfo=timezone.utc)


def _flex(alt_text="請立即就醫"):
    payload = {"type": "flex", "contents": {"type": "bubble"}}
    if alt_text is not None:
        payload["altText"] = alt_text
    return json.dumps(payload, ensure_ascii=False)


async def test_flex_json_reply_is_stored_as_a_short_placeholder():
    """整包卡片 JSON 會被 load_history 餵回 agent；存替身，兩邊紀錄都是。"""
    cache, log = FakeCache(), FakeConversationLog()
    service = LineMessageHistoryService(cache, conversation_log=log)

    await service.save_turn("U123", "我阿公昏迷", _flex(), "text", _WHEN)

    assert [m.content for m in cache.messages] == ["我阿公昏迷", "[已回覆卡片：請立即就醫]"]
    assert [m.content for m in log.messages] == ["我阿公昏迷", "[已回覆卡片：請立即就醫]"]
    assert cache.messages[1].message_type == "assistant_reply"


async def test_ununderstood_fallback_is_not_stored_as_an_ai_turn():
    """「我無法理解」是保底句不是回答；存了下一輪 agent 會學著再說一次。"""
    cache, log = FakeCache(), FakeConversationLog()
    service = LineMessageHistoryService(cache, conversation_log=log)

    await service.save_turn(
        "U123", "hello", t("line.fallback_ununderstood", "en"), "text", _WHEN
    )

    assert [m.content for m in cache.messages] == ["hello"]
    assert [m.content for m in log.messages] == ["hello"]


def test_placeholder_only_matches_tool_flex_json():
    assert flex_history_placeholder("建議多休息") is None
    assert flex_history_placeholder('{"a": 1}') is None
    assert flex_history_placeholder("{不是 JSON}") is None
    assert flex_history_placeholder('{"type": "text", "contents": {}}') is None
    assert flex_history_placeholder(_flex(alt_text=None)) == "[已回覆卡片]"
    assert flex_history_placeholder("  " + _flex() + "\n") == "[已回覆卡片：請立即就醫]"
