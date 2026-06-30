import os
import time
from pathlib import Path
from unittest.mock import MagicMock

from app.services.line_messaging import tts_service as tts_module
from app.services.line_messaging.tts_service import TTSService
from app.core.config import settings


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


def test_synthesize_via_n8n_webhook(monkeypatch):
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

    audio_bytes, audio_url, duration_ms = TTSService().synthesize(
        "hello", locale="zh-TW"
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
