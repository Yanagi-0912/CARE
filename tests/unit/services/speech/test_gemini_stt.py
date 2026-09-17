import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.speech import gemini_stt
from app.services.speech.gemini_stt import GeminiTranscriber


class FakeChatModel:
    def __init__(self, content="逐字稿", delay=0.0):
        self.content = content
        self.delay = delay
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        await asyncio.sleep(self.delay)
        return SimpleNamespace(content=self.content)


def _transcriber(model):
    return GeminiTranscriber(gemini_service=SimpleNamespace(chat_model=model))


@pytest.mark.asyncio
async def test_sends_audio_inline_with_language_in_prompt(tmp_path):
    p = tmp_path / "voice.m4a"
    p.write_bytes(b"audio-bytes")
    model = FakeChatModel(content="  Sáng nay tôi uống thuốc  ")

    out = await _transcriber(model).transcribe(p, "vi")

    assert out == "Sáng nay tôi uống thuốc"
    text_part, media_part = model.messages[0].content
    assert "Tiếng Việt" in text_part["text"]
    assert media_part == {"type": "media", "mime_type": "audio/m4a", "data": b"audio-bytes"}


@pytest.mark.asyncio
async def test_unknown_language_uses_mandarin_prompt(tmp_path):
    p = tmp_path / "voice.m4a"
    p.write_bytes(b"x")
    model = FakeChatModel()

    await _transcriber(model).transcribe(p, "ko")

    assert "繁體中文" in model.messages[0].content[0]["text"]


@pytest.mark.asyncio
async def test_slow_response_times_out(tmp_path):
    p = tmp_path / "voice.m4a"
    p.write_bytes(b"x")

    with patch.object(gemini_stt, "GEMINI_STT_TIMEOUT_SECONDS", 0.05):
        with pytest.raises(TimeoutError):
            await _transcriber(FakeChatModel(delay=1.0)).transcribe(p, "zh-TW")


def test_unavailable_without_api_key():
    with patch.object(gemini_stt.settings, "GEMINI_API_KEY", ""):
        assert GeminiTranscriber().available() is False


def test_uses_flash_lite_with_low_thinking():
    with patch.object(gemini_stt.settings, "GEMINI_API_KEY", "k"), \
         patch.object(gemini_stt, "GeminiService") as service:
        GeminiTranscriber()._service()

    assert service.call_args.kwargs["model_name"] == "gemini-3.5-flash-lite"
    assert service.call_args.kwargs["thinking_level"] == "low"
