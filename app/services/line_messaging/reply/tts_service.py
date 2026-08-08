import asyncio
import io
import logging
import time
import uuid
from pathlib import Path
from typing import Optional, Protocol, Tuple

import requests

from app.core.config import settings
from app.core.user_language import DEFAULT_USER_LANGUAGE, SUPPORTED_LANGUAGES

try:
    import edge_tts
except Exception:  # pragma: no cover - depends on optional runtime package
    edge_tts = None

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

# 六語系 → edge-tts voice 名稱。未知語言一律 fallback DEFAULT_USER_LANGUAGE。
VOICE_BY_LANGUAGE = {
    "zh-TW": "zh-TW-HsiaoChenNeural",
    "en": "en-US-AriaNeural",
    "ja": "ja-JP-NanamiNeural",
    "th": "th-TH-PremwadeeNeural",
    "vi": "vi-VN-HoaiMyNeural",
    "id": "id-ID-GadisNeural",
}

# voice_rate 檔位 → edge-tts rate 百分比（正負號、"+0%" 形式由 f"{percent:+d}%" 產生）。
RATE_PERCENT = {"slow": -25, "normal": 0, "fast": 25}
DEFAULT_VOICE_RATE = "normal"


class SpeechEngine(Protocol):
    """主要合成引擎介面（edge-tts）：以 voice/rate 產生語音位元組。"""

    async def synthesize(self, text: str, *, voice: str, rate: str) -> bytes: ...


class FallbackSpeechEngine(Protocol):
    """備援合成引擎介面（gTTS）：以語言代碼產生語音位元組。"""

    async def synthesize(self, text: str, *, language: str) -> bytes: ...


class EdgeTTSEngine:
    """正式 edge-tts 實作。"""

    async def synthesize(self, text: str, *, voice: str, rate: str) -> bytes:
        if edge_tts is None:
            raise RuntimeError("edge-tts is not available in the environment")
        communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
        return b"".join(
            [chunk["data"] async for chunk in communicate.stream() if chunk["type"] == "audio"]
        )


class GTTSEngine:
    """正式 gTTS 實作（fallback）。gTTS 直接吃 SUPPORTED_LANGUAGES 的 code。"""

    async def synthesize(self, text: str, *, language: str) -> bytes:
        if gTTS is None:
            raise RuntimeError("gTTS is not available in the environment")

        def _synthesize_sync() -> bytes:
            tts = gTTS(text=text, lang=language)
            buf = io.BytesIO()
            tts.write_to_fp(buf)
            buf.seek(0)
            return buf.read()

        return await asyncio.to_thread(_synthesize_sync)


class TTSService:
    """Text-to-speech service：edge-tts 為主引擎，gTTS 為備援。

    synthesize(text, language, voice_rate) -> (bytes, path_or_url, duration_ms)。
    """

    def __init__(
        self,
        engine: SpeechEngine = EdgeTTSEngine(),
        fallback_engine: FallbackSpeechEngine = GTTSEngine(),
    ) -> None:
        self._engine = engine
        self._fallback_engine = fallback_engine

    async def synthesize(
        self, text: str, language: str = "zh-TW", voice_rate: str = "normal"
    ) -> Tuple[bytes, str, Optional[int]]:
        """Synthesize and return (bytes, path, duration_ms).

        The second value is either a local file path or a public audio URL.
        """
        try:
            if settings.N8N_TTS_WEBHOOK_URL.strip():
                return await self._synthesize_via_n8n(text, language)

            TTS_TMP_DIR.mkdir(parents=True, exist_ok=True)
            self.cleanup_expired_audio_files()

            data = await self._synthesize_bytes(text, language, voice_rate)
            duration_ms = self._get_duration_ms(data, text)
            filename = f"tts_{uuid.uuid4().hex}.mp3"
            tmp_path = TTS_TMP_DIR / filename
            with tmp_path.open("wb") as f:
                f.write(data)
            logger.debug("TTS synthesized audio: %s, saved to %s", filename, tmp_path)
            return data, str(tmp_path), duration_ms
        except Exception:
            logger.exception("TTS synthesis failed")
            raise

    async def _synthesize_bytes(self, text: str, language: str, voice_rate: str) -> bytes:
        normalized_language = language if language in SUPPORTED_LANGUAGES else DEFAULT_USER_LANGUAGE
        voice = VOICE_BY_LANGUAGE.get(normalized_language, VOICE_BY_LANGUAGE[DEFAULT_USER_LANGUAGE])
        percent = RATE_PERCENT.get(voice_rate, RATE_PERCENT[DEFAULT_VOICE_RATE])
        rate = f"{percent:+d}%"
        try:
            return await self._engine.synthesize(text, voice=voice, rate=rate)
        except Exception:
            logger.warning(
                "edge-tts 合成失敗，改用 gTTS fallback：language=%s", normalized_language,
                exc_info=True,
            )
            return await self._fallback_engine.synthesize(text, language=normalized_language)

    def available(self) -> bool:
        return bool(settings.N8N_TTS_WEBHOOK_URL.strip()) or edge_tts is not None or gTTS is not None

    async def _synthesize_via_n8n(
        self, text: str, language: str = "zh-TW"
    ) -> Tuple[bytes, str, Optional[int]]:
        mapped_language = self._locale_to_language(language)
        payload = {
            "text": text,
            "locale": language,
            "language": mapped_language,
            "voice": settings.TTS_DEFAULT_VOICE or None,
        }
        headers = {"Content-Type": "application/json"}
        if settings.N8N_TTS_WEBHOOK_SECRET:
            headers["X-CARE-TTS-SECRET"] = settings.N8N_TTS_WEBHOOK_SECRET

        response = await asyncio.to_thread(
            requests.post,
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
            data.get("language") or mapped_language,
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
