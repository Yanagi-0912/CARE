import io
import logging
import time
import uuid
from pathlib import Path
from typing import Tuple, Optional

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

        Path currently points to a local tmp file where audio is written.
        """
        try:
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
            logger.info(f"TTS synthesized audio (gTTS): {filename}, saved to {tmp_path}")
            return data, str(tmp_path), duration_ms
        except Exception as e:
            logger.error(f"TTS synthesis failed: {e}")
            raise

    def available(self) -> bool:
        return gTTS is not None

    def _get_duration_ms(self, audio_data: bytes, text: str) -> int:
        if MP3 is not None:
            try:
                audio = MP3(io.BytesIO(audio_data))
                return max(DEFAULT_DURATION_MS, int(audio.info.length * 1000))
            except Exception as e:
                logger.warning(f"Failed to read MP3 duration: {e}")

        estimated_ms = len(text.strip()) * 250
        return max(DEFAULT_DURATION_MS, estimated_ms)

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
