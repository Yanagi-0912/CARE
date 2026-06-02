import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.line_messaging.message_service import LineMessageService
from app.services.line_messaging.shared.errors import LineTokenError, LineValidationError

@pytest.fixture
def mock_deps():
    return {
        "token_provider": MagicMock(),
        "medical_service": MagicMock(),
        "line_messaging_client": MagicMock(),
    }

@pytest.fixture
def svc(mock_deps):
    return LineMessageService(
        token_provider=mock_deps["token_provider"],
        medical_service=mock_deps["medical_service"],
        line_messaging_client=mock_deps["line_messaging_client"]
    )

@pytest.mark.asyncio
async def test_send_line_reply_success(svc, mock_deps):
    mock_deps["token_provider"].get_token.return_value = "secret_token"
    
    ok = await svc.send_line_reply("token", "hello", "user_1")
    
    assert ok is True
    mock_deps["line_messaging_client"].reply_message.assert_called_once()
    args = mock_deps["line_messaging_client"].reply_message.call_args[0]
    assert args[0] == "secret_token"
    assert args[1].reply_token == "token"
    assert args[1].messages[0].text == "hello"

@pytest.mark.asyncio
async def test_send_line_reply_token_error(svc, mock_deps):
    mock_deps["token_provider"].get_token.side_effect = LineTokenError("Expired")
    
    ok = await svc.send_line_reply("token", "hello")
    
    assert ok is False
    mock_deps["line_messaging_client"].reply_message.assert_not_called()

@pytest.mark.asyncio
async def test_send_line_reply_validation_error(svc, mock_deps):
    # 傳入空的 reply_token 會觸發 validate_reply_context 的 LineValidationError
    ok = await svc.send_line_reply("", "hello")
    
    assert ok is False
    mock_deps["line_messaging_client"].reply_message.assert_not_called()


@pytest.mark.asyncio
async def test_send_line_reply_non_string_conversion(svc, mock_deps):
    mock_deps["token_provider"].get_token.return_value = "secret_token"
    
    # Pass a list of dicts/strings/ints as message_text
    non_string_msg = ["hello", {"type": "text", "text": " world"}, 123]
    ok = await svc.send_line_reply("token", non_string_msg, "user_1")
    
    assert ok is True
    mock_deps["line_messaging_client"].reply_message.assert_called_once()
    args = mock_deps["line_messaging_client"].reply_message.call_args[0]
    assert args[1].messages[0].text == "hello world123"


