import io
import logging
import time
import uuid
from pathlib import Path
from typing import Tuple, Optional

import requests

from app.core.config import settings

try:
    from gtts import gTTS
except Exception:  # pragma: no cover - depends on optional runtime package
    gTTS = None

try:
    from mutagen.mp3 import MP3
except Exception:  # pragma: no cover - depends on optional runtime package
    MP3 = None

logger = logging.getLogger(__name__)

__all__ = ["TTSService"]

TTS_TMP_DIR = Path("app_data") / "tmp"
DEFAULT_DURATION_MS = 1_000
DEFAULT_TTS_FILE_TTL_SECONDS = 60 * 60


class TTSService:
    """Text-to-speech service backed by gTTS when available.

    synthesize(text, locale) -> (bytes, filename, duration_ms).
    """

    def synthesize(
        self, text: str, locale: str = "zh-TW"
    ) -> Tuple[bytes, str, Optional[int]]:
        """Synthesize and return (bytes, path, duration_ms).

        The second value is either a local file path or a public audio URL.
        """
        try:
            if settings.N8N_TTS_WEBHOOK_URL.strip():
                return self._synthesize_via_n8n(text, locale)

            # Use gTTS to synthesize (this is synchronous but fine for small payloads)
            if gTTS is None:
                raise RuntimeError("gTTS is not available in the environment")
            TTS_TMP_DIR.mkdir(parents=True, exist_ok=True)
            self.cleanup_expired_audio_files()
            tts = gTTS(text=text, lang=("zh-tw" if locale.startswith("zh") else "en"))
            buf = io.BytesIO()
            tts.write_to_fp(buf)
            buf.seek(0)
            data = buf.read()
            duration_ms = self._get_duration_ms(data, text)
            # write to temp file and return bytes
            filename = f"tts_{uuid.uuid4().hex}.mp3"
            tmp_path = TTS_TMP_DIR / filename
            with tmp_path.open("wb") as f:
                f.write(data)
            logger.debug("TTS synthesized audio (gTTS): %s, saved to %s", filename, tmp_path)
            return data, str(tmp_path), duration_ms
        except Exception:
            logger.exception("TTS synthesis failed")
            raise

    def available(self) -> bool:
        return bool(settings.N8N_TTS_WEBHOOK_URL.strip()) or gTTS is not None

    def _synthesize_via_n8n(
        self, text: str, locale: str = "zh-TW"
    ) -> Tuple[bytes, str, Optional[int]]:
        language = self._locale_to_language(locale)
        payload = {
            "text": text,
            "locale": locale,
            "language": language,
            "voice": settings.TTS_DEFAULT_VOICE or None,
        }
        headers = {"Content-Type": "application/json"}
        if settings.N8N_TTS_WEBHOOK_SECRET:
            headers["X-CARE-TTS-SECRET"] = settings.N8N_TTS_WEBHOOK_SECRET

        response = requests.post(
            settings.N8N_TTS_WEBHOOK_URL,
            json=payload,
            headers=headers,
            timeout=settings.N8N_TTS_TIMEOUT_SECONDS,
        )
        response.raise_for_status()

        data = response.json()
        audio_url = data.get("audio_url") or data.get("audioUrl")
        if not audio_url or not isinstance(audio_url, str):
            raise RuntimeError("n8n TTS response missing audio_url")

        duration_ms = data.get("duration_ms") or data.get("durationMs")
        if duration_ms is not None:
            duration_ms = int(duration_ms)

        logger.debug(
            "TTS synthesized via n8n: language=%s, voice=%s, url=%s",
            data.get("language") or language,
            data.get("voice") or settings.TTS_DEFAULT_VOICE or None,
            audio_url,
        )
        return b"", audio_url, duration_ms

    def _get_duration_ms(self, audio_data: bytes, text: str) -> int:
        if MP3 is not None:
            try:
                audio = MP3(io.BytesIO(audio_data))
                return max(DEFAULT_DURATION_MS, int(audio.info.length * 1000))
            except Exception as e:
                logger.warning(f"Failed to read MP3 duration: {e}")

        estimated_ms = len(text.strip()) * 250
        return max(DEFAULT_DURATION_MS, estimated_ms)

    @staticmethod
    def _locale_to_language(locale: str) -> str:
        normalized = (locale or "").lower()
        if normalized.startswith("zh"):
            return "zh"
        if normalized.startswith("en"):
            return "en"
        if normalized.startswith("ja"):
            return "ja"
        if normalized.startswith("ko"):
            return "ko"
        return normalized.split("-")[0] if normalized else "zh"

    def cleanup_expired_audio_files(
        self, max_age_seconds: int = DEFAULT_TTS_FILE_TTL_SECONDS
    ) -> None:
        cutoff = time.time() - max_age_seconds
        for audio_path in TTS_TMP_DIR.glob("tts_*.mp3"):
            try:
                if audio_path.is_file() and audio_path.stat().st_mtime < cutoff:
                    audio_path.unlink()
                    logger.info(f"Deleted expired TTS audio file: {audio_path}")
            except Exception as e:
                logger.warning(f"Failed to delete expired TTS audio file {audio_path}: {e}")
