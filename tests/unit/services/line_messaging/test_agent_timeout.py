"""agent 總逾時：超過 AGENT_TOTAL_TIMEOUT_SECONDS 回「稍後再問」、不存對話紀錄。"""

import asyncio
from datetime import datetime

import pytest
from linebot.v3.webhooks import (
    DeliveryContext,
    MessageEvent,
    TextMessageContent,
    UserSource,
)

from app.core.config import settings
from app.i18n.messages import t
from app.services.line_messaging.handler.message_handler import LineMessageHandler


class _HangingAgent:
    def __init__(self):
        self.cancelled = False

    async def invoke(self, **kwargs):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return {"response": "太慢了"}


class _History:
    def __init__(self):
        self.saved = []

    async def load_history(self, **kwargs):
        return []

    async def save_turn(self, **kwargs):
        self.saved.append(kwargs)


class _Profile:
    async def get_user_profile(self, user_id):
        return {"settings": {"language": "en", "voice_reply_enabled": True}}


class _Replier:
    def __init__(self):
        self.replies = []

    async def reply(self, **kwargs):
        self.replies.append(kwargs)
        return True


def _event():
    return MessageEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZTEST0000000000000000T1",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt",
        source=UserSource(type="user", userId="U_SLOW"),
        message=TextMessageContent(id="M1", text="這個藥可以吃嗎", quoteToken="q"),
    )


@pytest.mark.asyncio
async def test_agent_timeout_sends_busy_message_and_skips_history(monkeypatch, caplog):
    monkeypatch.setattr(settings, "AGENT_TOTAL_TIMEOUT_SECONDS", 0.02)
    agent, history, replier = _HangingAgent(), _History(), _Replier()
    handler = LineMessageHandler(
        agent=agent, history_service=history, user_profile_service=_Profile(), replier=replier
    )

    with caplog.at_level("INFO"):
        await handler.handle(_event())

    assert agent.cancelled  # wait_for 取消了還在跑的 agent
    assert len(replier.replies) == 1
    reply = replier.replies[0]
    assert reply["message_text"] == t("line.fallback_busy", language="en")
    assert reply["language"] == "en"
    assert reply["voice_reply_enabled"] is False
    assert history.saved == []
    assert any("stage=agent_timeout" in r.getMessage() for r in caplog.records)


def test_timeout_budget_is_reasonable():
    """來由見 config：RAG 45s + 其餘 Gemini 呼叫 ~25s + 餘裕；太小會把正常的
    RAG 題砍掉，太大就失去意義。"""
    assert 60 <= settings.AGENT_TOTAL_TIMEOUT_SECONDS <= 120


def test_busy_message_exists_in_every_language():
    from app.core.user_language import SUPPORTED_LANGUAGES
    from app.i18n.messages import _MESSAGES

    assert set(_MESSAGES["line.fallback_busy"]) == set(SUPPORTED_LANGUAGES)
