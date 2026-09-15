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


# ── 台語（nan-TW）：先改寫成台語漢字，再用 Taigi 台語 TTS 念 ─────────

from app.services.speech import audio as speech_audio


def _wav(seconds: float = 0.5, rate: int = 22_050) -> bytes:
    return speech_audio.pcm16_to_wav(bytes(int(seconds * rate) * 2), rate)


class FakeTaigiClient:
    def __init__(self, wav: Optional[bytes] = None, exc: Optional[Exception] = None, available=True):
        self.wav = wav or _wav()
        self.exc = exc
        self._available = available
        self.calls: list[dict] = []

    def available(self) -> bool:
        return self._available

    def synthesize_wav(self, text: str, *, voice_label: str, speed: float) -> bytes:
        self.calls.append({"text": text, "voice_label": voice_label, "speed": speed})
        if self.exc is not None:
            raise self.exc
        return self.wav


class FakeTaigiText:
    def __init__(self, exc: Optional[Exception] = None):
        self.exc = exc
        self.calls: list[str] = []

    async def to_taigi(self, text: str) -> str:
        self.calls.append(text)
        if self.exc is not None:
            raise self.exc
        return f"台語：{text}"


async def test_taiwanese_is_converted_then_spoken_by_taigi_as_mp3():
    engine = FakeSpeechEngine()
    taigi = FakeTaigiClient(wav=_wav(1.5))
    converter = FakeTaigiText()
    service = TTSService(
        engine=engine,
        fallback_engine=FakeFallbackEngine(),
        taigi_client=taigi,
        taigi_text_converter=converter,
    )

    data, path, duration_ms = await service.synthesize(
        "記得吃藥", language="nan-TW", voice_rate="slow", voice_gender="male"
    )
    try:
        assert converter.calls == ["記得吃藥"]
        assert taigi.calls == [{"text": "台語：記得吃藥", "voice_label": "normal_m2", "speed": 0.9}]
        assert engine.calls == []
        assert path.endswith(".mp3")
        assert Path(path).read_bytes() == data
        # 存下來的是解得開的 16 kHz mp3（轉檔成本見 TAIGI_MP3_SAMPLE_RATE），長度照 WAV 算
        _, rate = speech_audio.decode_to_pcm16_mono(Path(path))
        assert rate == 16_000
        assert abs(duration_ms - 1500) <= 10
    finally:
        _cleanup(path)


@pytest.mark.parametrize(
    "taigi,converter",
    [
        (FakeTaigiClient(exc=RuntimeError("HTTP 502")), FakeTaigiText()),
        (FakeTaigiClient(), FakeTaigiText(exc=RuntimeError("Gemini 400"))),
        (None, None),
    ],
    ids=["taigi-fails", "conversion-fails", "not-configured"],
)
async def test_taiwanese_falls_back_to_mandarin_edge_tts(taigi, converter):
    engine = FakeSpeechEngine()
    service = TTSService(
        engine=engine,
        fallback_engine=FakeFallbackEngine(),
        taigi_client=taigi,
        taigi_text_converter=converter,
    )

    _, path, _ = await service.synthesize("記得吃藥", language="nan-TW", voice_gender="female")
    try:
        assert engine.calls[0]["voice"] == "zh-TW-HsiaoChenNeural"
        assert engine.calls[0]["text"] == "記得吃藥"
    finally:
        _cleanup(path)


# 沒金鑰時不該先花一次 Gemini 改寫才發現念不了。
async def test_taiwanese_without_key_skips_conversion():
    converter = FakeTaigiText()
    service = TTSService(
        engine=FakeSpeechEngine(),
        fallback_engine=FakeFallbackEngine(),
        taigi_client=FakeTaigiClient(available=False),
        taigi_text_converter=converter,
    )

    _, path, _ = await service.synthesize("記得吃藥", language="nan-TW")
    try:
        assert converter.calls == []
    finally:
        _cleanup(path)


async def test_other_languages_never_touch_taigi():
    taigi = FakeTaigiClient()
    converter = FakeTaigiText()
    service = TTSService(
        engine=FakeSpeechEngine(),
        fallback_engine=FakeFallbackEngine(),
        taigi_client=taigi,
        taigi_text_converter=converter,
    )

    _, path, _ = await service.synthesize("hello", language="zh-TW")
    try:
        assert taigi.calls == []
        assert converter.calls == []
    finally:
        _cleanup(path)


# ── 音檔保存：30 天、清除最多一小時一次、公開網址 ─────────────────────


def test_default_retention_keeps_audio_for_30_days(monkeypatch):
    """與對話原文同樣保留 30 天：使用者回頭翻對話時，語音也要播得出來。"""
    test_dir = Path("app_data") / "tmp" / "tts_retention_test"
    test_dir.mkdir(parents=True, exist_ok=True)
    day = 24 * 60 * 60
    kept = test_dir / "tts_29_days.mp3"
    expired = test_dir / "tts_31_days.mp3"
    for path, age_days in ((kept, 29), (expired, 31)):
        path.write_bytes(b"mp3")
        mtime = time.time() - age_days * day
        os.utime(path, (mtime, mtime))
    monkeypatch.setattr(tts_module, "TTS_TMP_DIR", test_dir)

    try:
        TTSService().cleanup_expired_audio_files()

        assert kept.exists()
        assert not expired.exists()
    finally:
        kept.unlink(missing_ok=True)
        expired.unlink(missing_ok=True)
        test_dir.rmdir()


def test_expired_audio_is_cleaned_at_most_once_per_interval(monkeypatch):
    """30 天份的檔案每次合成都掃一遍，掃描時間會直接加在使用者的等待上。"""
    now = [1_000.0]
    service = TTSService(clock=lambda: now[0])
    cleaned_at: list[float] = []
    monkeypatch.setattr(
        service, "cleanup_expired_audio_files", lambda: cleaned_at.append(now[0])
    )

    paths = []
    try:
        paths.append(service._save_mp3(b"a", 1_000)[1])
        now[0] += tts_module.TTS_CLEANUP_INTERVAL_SECONDS - 1
        paths.append(service._save_mp3(b"b", 1_000)[1])
        now[0] += 1
        paths.append(service._save_mp3(b"c", 1_000)[1])
    finally:
        for path in paths:
            _cleanup(path)

    assert cleaned_at == [1_000.0, 1_000.0 + tts_module.TTS_CLEANUP_INTERVAL_SECONDS]


def test_public_audio_url_builds_link_for_local_file(monkeypatch):
    audio_file = Path("app_data") / "tmp" / "tts_public_url_test.mp3"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"mp3")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://care.example/")
    monkeypatch.setattr(settings, "TTS_AUDIO_URL_PATH", "/tts")

    try:
        assert tts_module.public_audio_url(str(audio_file)) == (
            "https://care.example/tts/tts_public_url_test.mp3"
        )
    finally:
        audio_file.unlink(missing_ok=True)


def test_public_audio_url_passes_existing_urls_through():
    url = "https://care.example/tts/tts_abc.mp3"

    assert tts_module.public_audio_url(url) == url


def test_public_audio_url_is_none_without_public_base_url(monkeypatch):
    audio_file = Path("app_data") / "tmp" / "tts_no_base_url_test.mp3"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"mp3")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")

    try:
        assert tts_module.public_audio_url(str(audio_file)) is None
    finally:
        audio_file.unlink(missing_ok=True)
