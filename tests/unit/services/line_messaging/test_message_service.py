import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from app.services.line_messaging.message_service import LineMessageService
from app.services.line_messaging.shared.errors import LineTokenError, LineValidationError
from app.core.config import settings

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


@pytest.mark.asyncio
async def test_send_line_reply_with_voice_adds_audio_message(
    mock_deps, monkeypatch
):
    mock_deps["token_provider"].get_token.return_value = "secret_token"
    audio_file = Path("app_data") / "tmp" / "tts_test.mp3"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"mp3")
    tts_service = MagicMock()
    tts_service.synthesize.return_value = (b"mp3", str(audio_file), 1234)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setattr(settings, "TTS_AUDIO_URL_PATH", "/tts")

    svc = LineMessageService(
        token_provider=mock_deps["token_provider"],
        medical_service=mock_deps["medical_service"],
        line_messaging_client=mock_deps["line_messaging_client"],
        tts_service=tts_service,
    )

    ok = await svc.send_line_reply("token", "hello", "user_1")

    assert ok is True
    tts_service.synthesize.assert_called_once_with("hello", locale="zh-TW")
    args = mock_deps["line_messaging_client"].reply_message.call_args[0]
    messages = args[1].messages
    assert len(messages) == 2
    assert messages[0].text == "hello"
    assert messages[1].type == "audio"
    assert messages[1].original_content_url == "https://example.com/tts/tts_test.mp3"
    assert messages[1].duration == 1234
    audio_file.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_send_line_reply_voice_disabled_skips_tts(mock_deps):
    mock_deps["token_provider"].get_token.return_value = "secret_token"
    tts_service = MagicMock()
    svc = LineMessageService(
        token_provider=mock_deps["token_provider"],
        medical_service=mock_deps["medical_service"],
        line_messaging_client=mock_deps["line_messaging_client"],
        tts_service=tts_service,
    )

    ok = await svc.send_line_reply(
        "token", "hello", "user_1", voice_reply_enabled=False
    )

    assert ok is True
    tts_service.synthesize.assert_not_called()
    args = mock_deps["line_messaging_client"].reply_message.call_args[0]
    assert len(args[1].messages) == 1


@pytest.mark.asyncio
async def test_send_line_reply_without_public_base_url_falls_back_to_text(
    mock_deps, monkeypatch
):
    mock_deps["token_provider"].get_token.return_value = "secret_token"
    audio_file = Path("app_data") / "tmp" / "tts_test_no_public_url.mp3"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"mp3")
    tts_service = MagicMock()
    tts_service.synthesize.return_value = (b"mp3", str(audio_file), 1234)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")

    svc = LineMessageService(
        token_provider=mock_deps["token_provider"],
        medical_service=mock_deps["medical_service"],
        line_messaging_client=mock_deps["line_messaging_client"],
        tts_service=tts_service,
    )

    ok = await svc.send_line_reply("token", "hello", "user_1")

    assert ok is True
    tts_service.synthesize.assert_called_once()
    args = mock_deps["line_messaging_client"].reply_message.call_args[0]
    assert len(args[1].messages) == 1
    audio_file.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_send_line_reply_with_n8n_audio_url_adds_audio_without_public_base_url(
    mock_deps, monkeypatch
):
    mock_deps["token_provider"].get_token.return_value = "secret_token"
    tts_service = MagicMock()
    tts_service.synthesize.return_value = (
        b"",
        "https://cdn.example/tts/n8n-test.mp3",
        3456,
    )
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")

    svc = LineMessageService(
        token_provider=mock_deps["token_provider"],
        medical_service=mock_deps["medical_service"],
        line_messaging_client=mock_deps["line_messaging_client"],
        tts_service=tts_service,
    )

    ok = await svc.send_line_reply("token", "hello", "user_1")

    assert ok is True
    args = mock_deps["line_messaging_client"].reply_message.call_args[0]
    messages = args[1].messages
    assert len(messages) == 2
    assert messages[1].type == "audio"
    assert messages[1].original_content_url == "https://cdn.example/tts/n8n-test.mp3"
    assert messages[1].duration == 3456
