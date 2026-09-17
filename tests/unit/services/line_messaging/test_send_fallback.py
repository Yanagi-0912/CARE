"""reply 失敗改 push、LINE 錯誤分類、401 清 token 快取。"""

from unittest.mock import MagicMock, patch

import pytest
from linebot.v3.messaging import TextMessage
from linebot.v3.messaging.exceptions import ApiException

from app.services.line_messaging.reply.reply import LineReplier
from app.services.line_messaging.send_result import (
    SendOutcome,
    classify_send_exception,
)
from tests.conftest import fake_line_token_manager


def _patched_api(messaging_api: MagicMock):
    return (
        patch("app.services.line_messaging.reply.reply.Configuration"),
        patch("app.services.line_messaging.reply.reply.ApiClient"),
        patch(
            "app.services.line_messaging.reply.reply.MessagingApi",
            return_value=messaging_api,
        ),
    )


@pytest.mark.parametrize(
    "status, outcome",
    [
        (429, SendOutcome.QUOTA_EXCEEDED),
        (401, SendOutcome.UNAUTHORIZED),
        (400, SendOutcome.REJECTED),
        (404, SendOutcome.REJECTED),
        (500, SendOutcome.TRANSIENT),
        (None, SendOutcome.TRANSIENT),
    ],
)
def test_classify_by_status(status, outcome):
    exc = ApiException(status=status, reason="x") if status else RuntimeError("boom")
    assert classify_send_exception(exc).outcome is outcome


@pytest.mark.asyncio
async def test_reply_token_rejected_falls_back_to_push():
    """reply token 過期（400）→ 同一組訊息改 push，使用者仍收得到。"""
    api = MagicMock()
    api.reply_message.side_effect = ApiException(status=400, reason="Invalid reply token")
    replier = LineReplier(token_manager=fake_line_token_manager("tok"))
    p1, p2, p3 = _patched_api(api)
    with p1, p2, p3:
        ok = await replier.reply(
            reply_token="rt", message_text="你好", user_id="U1", voice_reply_enabled=False
        )
    assert ok is True
    api.push_message.assert_called_once()
    pushed = api.push_message.call_args[0][0]
    assert pushed.to == "U1"
    assert isinstance(pushed.messages[0], TextMessage)
    assert pushed.messages[0].text == "你好"


@pytest.mark.asyncio
async def test_push_rejected_too_sends_plain_error_text():
    """reply 與 push 都被 400 拒（內容不合法）→ 至少推一句錯誤說明。"""
    api = MagicMock()
    api.reply_message.side_effect = ApiException(status=400, reason="bad")
    api.push_message.side_effect = [ApiException(status=400, reason="bad"), None]
    replier = LineReplier(token_manager=fake_line_token_manager("tok"))
    p1, p2, p3 = _patched_api(api)
    with p1, p2, p3:
        ok = await replier.reply(
            reply_token="rt", message_text="x", user_id="U1", voice_reply_enabled=False
        )
    assert ok is True
    assert api.push_message.call_count == 2
    second = api.push_message.call_args_list[1][0][0]
    assert second.messages[0].text != "x"


@pytest.mark.asyncio
async def test_quota_exceeded_does_not_push():
    api = MagicMock()
    api.reply_message.side_effect = ApiException(status=429, reason="quota")
    replier = LineReplier(token_manager=fake_line_token_manager("tok"))
    p1, p2, p3 = _patched_api(api)
    with p1, p2, p3:
        ok = await replier.reply(
            reply_token="rt", message_text="x", user_id="U1", voice_reply_enabled=False
        )
    assert ok is False
    api.push_message.assert_not_called()


@pytest.mark.asyncio
async def test_unauthorized_invalidates_token_cache():
    api = MagicMock()
    api.push_message.side_effect = ApiException(status=401, reason="unauthorized")
    tm = fake_line_token_manager("tok")
    replier = LineReplier(token_manager=tm)
    p1, p2, p3 = _patched_api(api)
    with p1, p2, p3:
        result = await replier.push_text_result("U1", "hi")
    assert result.outcome is SendOutcome.UNAUTHORIZED
    tm.invalidate.assert_called_once()


@pytest.mark.asyncio
async def test_sdk_call_carries_request_timeout():
    api = MagicMock()
    replier = LineReplier(token_manager=fake_line_token_manager("tok"))
    p1, p2, p3 = _patched_api(api)
    with p1, p2, p3:
        await replier.push_text("U1", "hi")
    assert api.push_message.call_args.kwargs["_request_timeout"] > 0
