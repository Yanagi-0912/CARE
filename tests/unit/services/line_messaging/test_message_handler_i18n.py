from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from linebot.v3.webhooks import (
    DeliveryContext,
    MessageEvent,
    TextMessageContent,
    UserSource,
)

from app.i18n.messages import t
from app.services.line_messaging.handler.message_handler import LineMessageHandler
from app.services.line_messaging.reply.reply import LineReplier


def _text_event(*, user_id: str = "U_EN") -> MessageEvent:
    return MessageEvent(
        timestamp=int(datetime.now().timestamp() * 1000),
        mode="active",
        webhookEventId="01HZTEST000000000000000000",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="rt",
        source=UserSource(type="user", userId=user_id),
        message=TextMessageContent(id="M1", text="hello", quoteToken="qt"),
    )


@pytest.fixture
def handler():
    token_manager = MagicMock()
    token_manager.get_token.return_value = "token"
    replier = LineReplier(token_manager=token_manager, tts_service=None)
    profile_service = MagicMock()
    profile_service.get_user_profile = AsyncMock(
        return_value={"settings": {"language": "en", "voice_reply_enabled": False}}
    )
    history_service = MagicMock()
    history_service.load_history = AsyncMock(return_value=[])
    history_service.save_turn = AsyncMock()
    agent = MagicMock()
    agent.invoke = AsyncMock(return_value={"response": ""})
    return LineMessageHandler(
        agent=agent,
        history_service=history_service,
        user_profile_service=profile_service,
        replier=replier,
    )


@pytest.mark.asyncio
async def test_message_handler_empty_response_uses_english_fallback(handler):
    with patch("app.services.line_messaging.reply.reply.Configuration"), patch(
        "app.services.line_messaging.reply.reply.ApiClient"
    ), patch(
        "app.services.line_messaging.reply.reply.MessagingApi"
    ) as mock_messaging_api:
        messaging_api = MagicMock()
        mock_messaging_api.return_value = messaging_api

        await handler.handle(_text_event())

    reply_req = messaging_api.reply_message.call_args[0][0]
    assert reply_req.messages[0].text == t("line.fallback_ununderstood", "en")
