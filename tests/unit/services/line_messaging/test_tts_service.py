import os
import time
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

from app.services.line_messaging.reply import tts_service as tts_module
from app.services.line_messaging.reply.tts_service import (
    EDGE_TTS_CONNECT_TIMEOUT_SECONDS,
    EDGE_TTS_RECEIVE_TIMEOUT_SECONDS,
    EdgeTTSEngine,
    TTSService,
)
from app.core.config import settings


class FakeSpeechEngine:
    """DI 用的假主引擎（取代 edge-tts），記錄呼叫參數並可模擬失敗。"""

    def __init__(self, result: bytes = b"edge-audio-bytes", exc: Optional[Exception] = None):
        self.result = result
        self.exc = exc
        self.calls: list[dict] = []

    async def synthesize(self, text: str, *, voice: str, rate: str) -> bytes:
        self.calls.append({"text": text, "voice": voice, "rate": rate})
        if self.exc is not None:
            raise self.exc
        return self.result


class FakeFallbackEngine:
    """DI 用的假備援引擎（取代 gTTS），記錄呼叫參數並可模擬失敗。"""

    def __init__(self, result: bytes = b"gtts-audio-bytes", exc: Optional[Exception] = None):
        self.result = result
        self.exc = exc
        self.calls: list[dict] = []

    async def synthesize(self, text: str, *, language: str) -> bytes:
        self.calls.append({"text": text, "language": language})
        if self.exc is not None:
            raise self.exc
        return self.result


def _cleanup(path: str) -> None:
    p = Path(path)
    if p.exists():
        p.unlink()


def test_cleanup_expired_audio_files_removes_only_old_tts_files(monkeypatch):
    test_dir = Path("app_data") / "tmp" / "tts_cleanup_test"
    test_dir.mkdir(parents=True, exist_ok=True)
    old_tts = test_dir / "tts_old.mp3"
    fresh_tts = test_dir / "tts_fresh.mp3"
    other_file = test_dir / "other_old.mp3"

    old_tts.write_bytes(b"old")
    fresh_tts.write_bytes(b"fresh")
    other_file.write_bytes(b"other")

    old_time = time.time() - 7200
    os.utime(old_tts, (old_time, old_time))
    os.utime(other_file, (old_time, old_time))

    monkeypatch.setattr(tts_module, "TTS_TMP_DIR", test_dir)

    try:
        TTSService().cleanup_expired_audio_files(max_age_seconds=3600)

        assert not old_tts.exists()
        assert fresh_tts.exists()
        assert other_file.exists()
    finally:
        old_tts.unlink(missing_ok=True)
        fresh_tts.unlink(missing_ok=True)
        other_file.unlink(missing_ok=True)
        test_dir.rmdir()


async def test_synthesize_via_n8n_webhook(monkeypatch):
    monkeypatch.setattr(settings, "N8N_TTS_WEBHOOK_URL", "https://n8n.example/webhook/tts")
    monkeypatch.setattr(settings, "N8N_TTS_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(settings, "N8N_TTS_TIMEOUT_SECONDS", 7)
    monkeypatch.setattr(settings, "TTS_DEFAULT_VOICE", "female_a")

    response = MagicMock()
    response.json.return_value = {
        "audio_url": "https://cdn.example/tts/test.mp3",
        "duration_ms": 2345,
        "language": "zh",
        "voice": "female_a",
    }
    response.raise_for_status.return_value = None
    post = MagicMock(return_value=response)
    monkeypatch.setattr(tts_module.requests, "post", post)

    audio_bytes, audio_url, duration_ms = await TTSService().synthesize(
        "hello", language="zh-TW"
    )

    assert audio_bytes == b""
    assert audio_url == "https://cdn.example/tts/test.mp3"
    assert duration_ms == 2345
    post.assert_called_once_with(
        "https://n8n.example/webhook/tts",
        json={
            "text": "hello",
            "locale": "zh-TW",
            "language": "zh",
            "voice": "female_a",
        },
        headers={
            "Content-Type": "application/json",
            "X-CARE-TTS-SECRET": "secret",
        },
        timeout=7,
    )


@pytest.mark.parametrize(
    "language,expected_voice",
    [
        ("zh-TW", "zh-TW-HsiaoChenNeural"),
        ("en", "en-US-AriaNeural"),
        ("ja", "ja-JP-NanamiNeural"),
        ("th", "th-TH-PremwadeeNeural"),
        ("vi", "vi-VN-HoaiMyNeural"),
        ("id", "id-ID-GadisNeural"),
    ],
)
async def test_synthesize_maps_language_to_configured_voice(language, expected_voice):
    engine = FakeSpeechEngine()
    service = TTSService(engine=engine, fallback_engine=FakeFallbackEngine())

    _, path, _ = await service.synthesize("hello", language=language, voice_rate="normal")
    try:
        assert engine.calls[0]["voice"] == expected_voice
    finally:
        _cleanup(path)


@pytest.mark.parametrize(
    "language,voice_gender,expected_voice",
    [
        ("zh-TW", "female", "zh-TW-HsiaoChenNeural"),
        ("zh-TW", "male", "zh-TW-YunJheNeural"),
        ("en", "female", "en-US-AriaNeural"),
        ("en", "male", "en-US-AndrewNeural"),
        ("ja", "female", "ja-JP-NanamiNeural"),
        ("ja", "male", "ja-JP-KeitaNeural"),
        ("th", "female", "th-TH-PremwadeeNeural"),
        ("th", "male", "th-TH-NiwatNeural"),
        ("vi", "female", "vi-VN-HoaiMyNeural"),
        ("vi", "male", "vi-VN-NamMinhNeural"),
        ("id", "female", "id-ID-GadisNeural"),
        ("id", "male", "id-ID-ArdiNeural"),
    ],
)
async def test_synthesize_maps_language_and_gender_to_configured_voice(
    language, voice_gender, expected_voice
):
    engine = FakeSpeechEngine()
    service = TTSService(engine=engine, fallback_engine=FakeFallbackEngine())

    _, path, _ = await service.synthesize(
        "hello", language=language, voice_rate="normal", voice_gender=voice_gender
    )
    try:
        assert engine.calls[0]["voice"] == expected_voice
    finally:
        _cleanup(path)


async def test_synthesize_unknown_voice_gender_falls_back_to_female():
    engine = FakeSpeechEngine()
    service = TTSService(engine=engine, fallback_engine=FakeFallbackEngine())

    _, path, _ = await service.synthesize(
        "hello", language="ja", voice_rate="normal", voice_gender="nonbinary"
    )
    try:
        assert engine.calls[0]["voice"] == "ja-JP-NanamiNeural"
    finally:
        _cleanup(path)


async def test_synthesize_default_voice_gender_matches_existing_female_voice():
    engine = FakeSpeechEngine()
    service = TTSService(engine=engine, fallback_engine=FakeFallbackEngine())

    _, path, _ = await service.synthesize("hello", language="zh-TW", voice_rate="normal")
    try:
        assert engine.calls[0]["voice"] == "zh-TW-HsiaoChenNeural"
    finally:
        _cleanup(path)


async def test_synthesize_unknown_language_with_male_gender_falls_back_to_zh_tw_male():
    engine = FakeSpeechEngine()
    service = TTSService(engine=engine, fallback_engine=FakeFallbackEngine())

    _, path, _ = await service.synthesize(
        "hello", language="fr-FR", voice_rate="normal", voice_gender="male"
    )
    try:
        assert engine.calls[0]["voice"] == "zh-TW-YunJheNeural"
    finally:
        _cleanup(path)


@pytest.mark.parametrize(
    "voice_rate,expected_rate",
    [
        ("slow", "-25%"),
        ("normal", "+0%"),
        ("fast", "+25%"),
    ],
)
async def test_synthesize_rate_percent_string_format(voice_rate, expected_rate):
    engine = FakeSpeechEngine()
    service = TTSService(engine=engine, fallback_engine=FakeFallbackEngine())

    _, path, _ = await service.synthesize("hello", language="en", voice_rate=voice_rate)
    try:
        assert engine.calls[0]["rate"] == expected_rate
    finally:
        _cleanup(path)


async def test_synthesize_unknown_language_falls_back_to_zh_tw():
    engine = FakeSpeechEngine()
    service = TTSService(engine=engine, fallback_engine=FakeFallbackEngine())

    _, path, _ = await service.synthesize("hello", language="fr-FR", voice_rate="normal")
    try:
        assert engine.calls[0]["voice"] == "zh-TW-HsiaoChenNeural"
    finally:
        _cleanup(path)


async def test_synthesize_empty_language_falls_back_to_zh_tw():
    engine = FakeSpeechEngine()
    service = TTSService(engine=engine, fallback_engine=FakeFallbackEngine())

    _, path, _ = await service.synthesize("hello", language="", voice_rate="normal")
    try:
        assert engine.calls[0]["voice"] == "zh-TW-HsiaoChenNeural"
    finally:
        _cleanup(path)


async def test_synthesize_unknown_voice_rate_falls_back_to_normal():
    engine = FakeSpeechEngine()
    service = TTSService(engine=engine, fallback_engine=FakeFallbackEngine())

    _, path, _ = await service.synthesize("hello", language="en", voice_rate="ludicrous")
    try:
        assert engine.calls[0]["rate"] == "+0%"
    finally:
        _cleanup(path)


async def test_synthesize_falls_back_to_gtts_when_edge_tts_fails():
    engine = FakeSpeechEngine(exc=RuntimeError("edge-tts unavailable"))
    fallback = FakeFallbackEngine(result=b"gtts-bytes")
    service = TTSService(engine=engine, fallback_engine=fallback)

    audio_bytes, path, _ = await service.synthesize("hello", language="ja", voice_rate="normal")
    try:
        assert audio_bytes == b"gtts-bytes"
        assert fallback.calls[0]["language"] == "ja"
        assert fallback.calls[0]["text"] == "hello"
    finally:
        _cleanup(path)


def test_edge_tts_engine_builds_communicate_with_short_timeouts():
    """真正的 EdgeTTSEngine 建立 edge_tts.Communicate 時必須帶上明確的短 timeout，
    這樣微軟端點卡住時才能快速失敗轉往 gTTS 備援，而不是沿用 edge-tts 預設的
    connect_timeout=10／receive_timeout=60（合計最壞情況會逼近甚至超過 LINE
    reply token 的有效期限）。這裡直接使用真實安裝的 edge_tts 套件建構物件並讀取
    其 aiohttp.ClientTimeout，不需要送出任何網路請求，因此不需要 monkey patch。
    """
    communicate = EdgeTTSEngine._build_communicate(
        "hello", voice="en-US-AriaNeural", rate="+0%"
    )

    assert communicate.session_timeout.sock_connect == EDGE_TTS_CONNECT_TIMEOUT_SECONDS
    assert communicate.session_timeout.sock_read == EDGE_TTS_RECEIVE_TIMEOUT_SECONDS
    # 明確短於 edge-tts 的預設值（connect_timeout=10, receive_timeout=60），
    # 確保這不是巧合等於預設值。
    assert EDGE_TTS_CONNECT_TIMEOUT_SECONDS < 10
    assert EDGE_TTS_RECEIVE_TIMEOUT_SECONDS < 60


async def test_synthesize_raises_when_both_engines_fail():
    engine = FakeSpeechEngine(exc=RuntimeError("edge-tts unavailable"))
    fallback = FakeFallbackEngine(exc=RuntimeError("gtts unavailable"))
    service = TTSService(engine=engine, fallback_engine=fallback)

    with pytest.raises(RuntimeError, match="gtts unavailable"):
        await service.synthesize("hello", language="en", voice_rate="normal")
