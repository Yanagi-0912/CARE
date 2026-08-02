from unittest.mock import MagicMock, patch

import pytest

from app.i18n.messages import t
from app.services.line_messaging.reply.reply import LineReplier


@pytest.fixture
def replier():
    return LineReplier(token_manager=MagicMock(), tts_service=None)


@pytest.mark.asyncio
async def test_reply_location_qr_label_uses_japanese(replier):
    replier._token_manager.get_token.return_value = "token"

    with patch("app.services.line_messaging.reply.reply.Configuration"), patch(
        "app.services.line_messaging.reply.reply.ApiClient"
    ), patch(
        "app.services.line_messaging.reply.reply.MessagingApi"
    ) as mock_messaging_api:
        messaging_api = MagicMock()
        mock_messaging_api.return_value = messaging_api

        ok = await replier.reply(
            reply_token="rt",
            message_text="please share location",
            user_id="U1",
            request_location=True,
            voice_reply_enabled=False,
            language="ja",
        )

    assert ok is True
    reply_req = messaging_api.reply_message.call_args[0][0]
    qr_label = reply_req.messages[0].quick_reply.items[0].action.label
    assert qr_label == t("location.share_qr_label", "ja")
