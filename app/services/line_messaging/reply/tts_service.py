import asyncio
import io
import logging
import time
import uuid
from pathlib import Path
from typing import Callable, Optional, Protocol, Tuple
from urllib.parse import quote

import requests

from app.core.config import settings
from app.core.request_logging import stage_timer
from app.core.user_language import (
    DEFAULT_USER_LANGUAGE,
    SUPPORTED_LANGUAGES,
    TAIWANESE_LANGUAGE,
)
from app.services.gemini.services.gemini_service import GeminiService
from app.services.speech import audio
from app.services.speech.taigi_client import (
    DEFAULT_SPEED as TAIGI_DEFAULT_SPEED,
    DEFAULT_VOICE_LABEL as TAIGI_DEFAULT_VOICE_LABEL,
    SPEED_BY_RATE as TAIGI_SPEED_BY_RATE,
    VOICE_LABEL_BY_GENDER as TAIGI_VOICE_LABEL_BY_GENDER,
    TaigiClient,
)
from app.services.speech.taigi_text import (
    TAIGI_TEXT_MODEL,
    TAIGI_TEXT_THINKING_LEVEL,
    TaigiTextConverter,
)

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

__all__ = ["TTSService", "build_local_tts_service", "public_audio_url"]

TTS_TMP_DIR = Path("app_data") / "tmp"
DEFAULT_DURATION_MS = 1_000
# 音檔保存期限與對話原文相同（conversation_log_repository：原文保留 30 天）：使用者回頭
# 翻對話時文字還在，語音也要播得出來。以前只放 1 小時，而且檔案在 backend 容器裡，部署
# 換 pod 就全丟——2026-09-15 10:55 送出的語音，10:56 部署後 11:00 按播放，LINE 來拿檔
# 得到 404，畫面顯示「語音訊息的保存期限已過」。現在正式環境由 care-tts（app/tts_main.py）
# 存在 PVC。
DEFAULT_TTS_FILE_TTL_SECONDS = 30 * 24 * 60 * 60
# 清過期檔最多一小時一次：清除是在合成路徑上同步做的，30 天份的檔案若每次合成都整個
# 目錄掃一遍，掃描時間就直接加在使用者的等待上；期限以天計，晚一小時刪沒有差別。
TTS_CLEANUP_INTERVAL_SECONDS = 60 * 60

# 六語系 → 性別 → edge-tts voice 名稱。未知語言一律 fallback DEFAULT_USER_LANGUAGE，
# 未知性別一律 fallback DEFAULT_VOICE_GENDER。
VOICE_BY_LANGUAGE = {
    "zh-TW": {"female": "zh-TW-HsiaoChenNeural", "male": "zh-TW-YunJheNeural"},
    "en": {"female": "en-US-AriaNeural", "male": "en-US-AndrewNeural"},
    "ja": {"female": "ja-JP-NanamiNeural", "male": "ja-JP-KeitaNeural"},
    "th": {"female": "th-TH-PremwadeeNeural", "male": "th-TH-NiwatNeural"},
    "vi": {"female": "vi-VN-HoaiMyNeural", "male": "vi-VN-NamMinhNeural"},
    "id": {"female": "id-ID-GadisNeural", "male": "id-ID-ArdiNeural"},
}

DEFAULT_VOICE_GENDER = "female"

# voice_rate 檔位 → edge-tts rate 百分比（正負號、"+0%" 形式由 f"{percent:+d}%" 產生）。
RATE_PERCENT = {"slow": -25, "normal": 0, "fast": 25}
DEFAULT_VOICE_RATE = "normal"

# edge-tts 預設 connect_timeout=10、receive_timeout=60（見 edge_tts.Communicate 簽名）。
# 合成是在送出任何 LINE 訊息之前被 await 的，reply token 約一分鐘就會過期，
# 所以主引擎必須能快速失敗並轉往 gTTS 備援，不能沿用預設值。
EDGE_TTS_CONNECT_TIMEOUT_SECONDS = 5
EDGE_TTS_RECEIVE_TIMEOUT_SECONDS = 15

# 台語 TTS 回 WAV（22,050 Hz），轉成 48 kbps 單聲道 mp3 給 LINE（edge-tts 送的也是
# 48 kbps 單聲道：audio-24khz-48kbitrate-mono-mp3）。轉檔在正式環境很貴：backend 的
# CPU limit 是 500m，2026-09-14 在 pod 內實測 110 秒音檔用預設設定要 5.0 秒，降到
# 16 kHz、LAME 品質等級 7 只要 1.5 秒。本機把同一段念稿轉檔後丟回台語 STT，相似度
# 預設 0.92、16 kHz＋等級 7 也是 0.92。16 kHz 與 edge-tts 的 24 kHz 同屬 MPEG-2 的
# mp3 取樣率，LINE 本來就在播這一類。
TAIGI_MP3_BIT_RATE = 48_000
TAIGI_MP3_SAMPLE_RATE = 16_000
TAIGI_MP3_COMPRESSION_LEVEL = 7


class SpeechEngine(Protocol):
    """主要合成引擎介面（edge-tts）：以 voice/rate 產生語音位元組。"""

    async def synthesize(self, text: str, *, voice: str, rate: str) -> bytes: ...


class FallbackSpeechEngine(Protocol):
    """備援合成引擎介面（gTTS）：以語言代碼產生語音位元組。"""

    async def synthesize(self, text: str, *, language: str) -> bytes: ...


class TaigiTextConverterLike(Protocol):
    """華語 → 台語漢字（app/services/speech/taigi_text.TaigiTextConverter）。"""

    async def to_taigi(self, text: str) -> str: ...


class EdgeTTSEngine:
    """正式 edge-tts 實作。"""

    async def synthesize(self, text: str, *, voice: str, rate: str) -> bytes:
        if edge_tts is None:
            raise RuntimeError("edge-tts is not available in the environment")
        communicate = self._build_communicate(text, voice=voice, rate=rate)
        return b"".join(
            [chunk["data"] async for chunk in communicate.stream() if chunk["type"] == "audio"]
        )

    @staticmethod
    def _build_communicate(text: str, *, voice: str, rate: str) -> "edge_tts.Communicate":
        """建立 edge_tts.Communicate，明確帶入短 timeout 以確保能快速轉往 gTTS 備援。"""
        return edge_tts.Communicate(
            text,
            voice=voice,
            rate=rate,
            connect_timeout=EDGE_TTS_CONNECT_TIMEOUT_SECONDS,
            receive_timeout=EDGE_TTS_RECEIVE_TIMEOUT_SECONDS,
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

    語言是台語（nan-TW）時先把華語改寫成台語漢字、交給 Taigi 台語 TTS 念；
    任何一步失敗就改用 zh-TW 念國語——文字回覆本來就是華語，有聲音總比沒有好。

    synthesize(text, language, voice_rate, voice_gender) -> (bytes, path_or_url, duration_ms)。
    """

    def __init__(
        self,
        engine: SpeechEngine = EdgeTTSEngine(),
        fallback_engine: FallbackSpeechEngine = GTTSEngine(),
        taigi_client: Optional[TaigiClient] = None,
        taigi_text_converter: Optional[TaigiTextConverterLike] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._fallback_engine = fallback_engine
        self._taigi_client = taigi_client
        self._taigi_text_converter = taigi_text_converter
        self._clock = clock
        self._last_cleanup_at: Optional[float] = None

    async def synthesize(
        self,
        text: str,
        language: str = "zh-TW",
        voice_rate: str = "normal",
        voice_gender: str = DEFAULT_VOICE_GENDER,
    ) -> Tuple[bytes, str, Optional[int]]:
        """Synthesize and return (bytes, path, duration_ms).

        The second value is either a local file path or a public audio URL.
        """
        try:
            if language == TAIWANESE_LANGUAGE:
                taiwanese = await self._synthesize_taiwanese_or_none(
                    text, voice_rate, voice_gender
                )
                if taiwanese is not None:
                    return taiwanese
                language = DEFAULT_USER_LANGUAGE

            if settings.N8N_TTS_WEBHOOK_URL.strip():
                return await self._synthesize_via_n8n(text, language)

            data = await self._synthesize_bytes(text, language, voice_rate, voice_gender)
            return self._save_mp3(data, self._get_duration_ms(data, text))
        except Exception:
            logger.exception("TTS synthesis failed")
            raise

    def _save_mp3(self, data: bytes, duration_ms: int) -> Tuple[bytes, str, int]:
        TTS_TMP_DIR.mkdir(parents=True, exist_ok=True)
        self._cleanup_expired_audio_files_if_due()
        filename = f"tts_{uuid.uuid4().hex}.mp3"
        tmp_path = TTS_TMP_DIR / filename
        with tmp_path.open("wb") as f:
            f.write(data)
        logger.debug("TTS synthesized audio: %s, saved to %s", filename, tmp_path)
        return data, str(tmp_path), duration_ms

    async def _synthesize_taiwanese_or_none(
        self, text: str, voice_rate: str, voice_gender: str
    ) -> Optional[Tuple[bytes, str, int]]:
        client = self._taigi_client
        converter = self._taigi_text_converter
        if client is None or converter is None or not client.available():
            logger.warning("台語 TTS 未設定（TAIGI_API_KEY），改念國語")
            return None
        try:
            with stage_timer(logger, "taigi_text", chars=len(text or ""), ok="False") as t_text:
                taigi_text = await converter.to_taigi(text)
                t_text["ok"] = "True"
            with stage_timer(logger, "taigi_tts", chars=len(taigi_text), ok="False") as t_tts:
                wav = await asyncio.to_thread(
                    client.synthesize_wav,
                    taigi_text,
                    voice_label=TAIGI_VOICE_LABEL_BY_GENDER.get(
                        voice_gender, TAIGI_DEFAULT_VOICE_LABEL
                    ),
                    speed=TAIGI_SPEED_BY_RATE.get(voice_rate, TAIGI_DEFAULT_SPEED),
                )
                t_tts["ok"] = "True"
            pcm, rate = await asyncio.to_thread(
                audio.decode_to_pcm16_mono, io.BytesIO(wav), TAIGI_MP3_SAMPLE_RATE
            )
            mp3 = await asyncio.to_thread(
                audio.encode_mp3,
                pcm,
                rate,
                bit_rate=TAIGI_MP3_BIT_RATE,
                compression_level=TAIGI_MP3_COMPRESSION_LEVEL,
            )
        except Exception:
            logger.warning("台語 TTS 失敗，改念國語", exc_info=True)
            return None
        return self._save_mp3(mp3, max(DEFAULT_DURATION_MS, audio.pcm_duration_ms(pcm, rate)))

    async def _synthesize_bytes(
        self, text: str, language: str, voice_rate: str, voice_gender: str = DEFAULT_VOICE_GENDER
    ) -> bytes:
        normalized_language = language if language in SUPPORTED_LANGUAGES else DEFAULT_USER_LANGUAGE
        voices_by_gender = VOICE_BY_LANGUAGE.get(
            normalized_language, VOICE_BY_LANGUAGE[DEFAULT_USER_LANGUAGE]
        )
        voice = voices_by_gender.get(voice_gender) or voices_by_gender[DEFAULT_VOICE_GENDER]
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

    def _cleanup_expired_audio_files_if_due(self) -> None:
        now = self._clock()
        if (
            self._last_cleanup_at is not None
            and now - self._last_cleanup_at < TTS_CLEANUP_INTERVAL_SECONDS
        ):
            return
        self._last_cleanup_at = now
        self.cleanup_expired_audio_files()

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


def public_audio_url(output: str) -> Optional[str]:
    """合成結果 → LINE 下載得到的公開網址。

    `output` 是 TTSService.synthesize 回傳的第二個值：已經是網址（n8n、care-tts）就原樣
    回傳；本地檔案則接在 PUBLIC_BASE_URL 與 TTS_AUDIO_URL_PATH 之後。PUBLIC_BASE_URL
    沒設或檔案不在時回 None，呼叫端就不送語音。
    """
    if output.startswith(("https://", "http" + "://")):
        return output

    audio_path = Path(output)
    if not settings.PUBLIC_BASE_URL.strip():
        logger.warning("PUBLIC_BASE_URL is not set; skipping LINE audio reply.")
        return None
    if not audio_path.exists():
        logger.warning("TTS output file not found: %s", audio_path)
        return None

    audio_url_path = settings.TTS_AUDIO_URL_PATH.strip("/") or "tts"
    return (
        f"{settings.PUBLIC_BASE_URL.rstrip('/')}/"
        f"{audio_url_path}/{quote(audio_path.name)}"
    )


def build_local_tts_service() -> TTSService:
    """在本行程合成的 TTSService。

    backend（沒設 TTS_SERVICE_URL 時）與 care-tts（app/tts_main.py）共用這一份組裝，兩邊
    的台語設定才不會分岔。語言選台語的使用者：語音回覆先改寫成台語漢字（flash-lite、低 thinking，
    理由見 taigi_text.TAIGI_TEXT_MODEL／TAIGI_TEXT_THINKING_LEVEL），再用 Taigi 台語 TTS 念。
    """
    return TTSService(
        taigi_client=TaigiClient(),
        taigi_text_converter=TaigiTextConverter(
            GeminiService(
                api_key=settings.GEMINI_API_KEY,
                model_name=TAIGI_TEXT_MODEL,
                thinking_level=TAIGI_TEXT_THINKING_LEVEL,
            )
        ),
    )
