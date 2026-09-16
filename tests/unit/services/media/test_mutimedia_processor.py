import array
import asyncio
import io
import math
import time
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from app.core.user_language import reset_request_language, set_request_language
from app.services.media.mutimedia_processor import NO_CONTENT_TEXT, MediaProcessorService
from app.services.speech import audio as speech_audio

class FakeGetResponse:
    def __init__(self, headers=None, chunks=None, status_code=200):
        self.headers = headers or {}
        self._chunks = chunks or [b"abc"]
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=8192):
        for c in self._chunks:
            yield c

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

class FakePostResponse:
    def __init__(self, headers=None, text="", payload=None, status_code=200):
        self.headers = headers or {}
        self.text = text
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

@pytest.fixture
def svc():
    return MediaProcessorService()

@pytest.mark.asyncio
async def test_process_media_success_and_cleanup(svc, tmp_path):
    token_mgr = MagicMock()
    token_mgr.get_token.return_value = "t"
    with patch("app.services.media.mutimedia_processor.TMP_DIR", tmp_path), \
         patch(
             "app.dependencies.get_line_token_manager",
             return_value=token_mgr,
         ), \
         patch("app.services.media.mutimedia_processor.requests.get", return_value=FakeGetResponse(
             headers={"Content-Type": "image/jpeg", "Content-Length": "3"},
             chunks=[b"abc"],
         )), \
         patch.object(svc, "_extract_user_text_via_webhook", return_value="辨識結果"):
        out = await svc.process_media("mid", "image", user_id="U1")
        assert out == "辨識結果"
        assert len(list(tmp_path.glob("*"))) == 0

def test_download_rejects_bad_media_type(svc):
    with pytest.raises(ValueError, match="Unsupported media type"):
        svc._download_media_to_tmp("mid", "unknown")

def test_extract_json_user_text(svc, tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"x")
    with patch("app.services.media.mutimedia_processor.MEDIA_PARSE_WEBHOOK_URL", "https://x"), \
        patch("app.services.media.mutimedia_processor.requests.post", return_value=FakePostResponse(
             headers={"Content-Type": "application/json"},
             text='{"user_text":"hello"}',
             payload={"user_text": "hello"},
         )):
        assert svc._extract_user_text_via_webhook(p) == "hello"


@pytest.mark.asyncio
async def test_process_media_does_not_block_event_loop(svc, tmp_path):
    """底層 helper 是同步阻塞的，但 process_media 期間事件迴圈必須仍能推進。

    這是本檔案唯一真正的回歸守門員：helper 用的是同步 requests，一旦有人把
    `await asyncio.to_thread(...)` 改回直接呼叫，事件迴圈就會被鎖住最長
    WEBHOOK_TIMEOUT_SECONDS（120 秒），期間所有 LINE 訊息、LIFF API 與背景
    排程全部停擺，連 /health 都回不了。其他測試驗證的是回傳值，抓不到這件事。

    做法：讓 webhook 那一步同步睡 BLOCK_SECONDS，同時跑一個每 TICK 秒加一的
    計數器。事件迴圈沒被鎖住的話，計數器會在這段期間內持續前進。
    """
    BLOCK_SECONDS = 0.3
    TICK = 0.01

    ticks = 0

    async def _heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(TICK)
            ticks += 1

    def _blocking_webhook(_path: Path) -> str:
        time.sleep(BLOCK_SECONDS)  # 模擬同步 requests.post 的阻塞
        return "辨識結果"

    token_mgr = MagicMock()
    token_mgr.get_token.return_value = "t"

    heartbeat = asyncio.create_task(_heartbeat())
    try:
        with patch("app.services.media.mutimedia_processor.TMP_DIR", tmp_path), \
             patch("app.dependencies.get_line_token_manager", return_value=token_mgr), \
             patch("app.services.media.mutimedia_processor.requests.get", return_value=FakeGetResponse(
                 headers={"Content-Type": "image/jpeg", "Content-Length": "3"},
                 chunks=[b"abc"],
             )), \
             patch.object(svc, "_extract_user_text_via_webhook", side_effect=_blocking_webhook):
            out = await svc.process_media("mid", "image", user_id="U1")
    finally:
        heartbeat.cancel()

    assert out == "辨識結果"
    # 阻塞期間至少該跑掉一半的 tick；保守取 1/3 以避免 CI 上的排程抖動誤判。
    assert ticks >= (BLOCK_SECONDS / TICK) / 3, (
        f"事件迴圈在 process_media 期間被阻塞：只前進了 {ticks} 個 tick"
    )


# 語音送 n8n 時附上使用者的語言，讓 faster-whisper 不必自己猜。
# 沒有提示時，small 模型把真實的 LINE 語音判成緬甸語、日文而轉出亂碼；亂碼再觸發
# temperature 重解碼，2026-09-14 一則 9.7 秒的語音在 2 核上轉了 133 秒，
# 超過 WEBHOOK_TIMEOUT_SECONDS（120 秒）而失敗。給了語言後同一段只要 4.4 秒。
@pytest.mark.parametrize("lang", ["zh-TW", "id", "vi"])
def test_webhook_sends_user_language_for_asr(svc, tmp_path, lang):
    p = tmp_path / "a.m4a"
    p.write_bytes(b"x")
    token = set_request_language(lang)
    try:
        with patch("app.services.media.mutimedia_processor.MEDIA_PARSE_WEBHOOK_URL", "https://x"), \
             patch("app.services.media.mutimedia_processor.requests.post", return_value=FakePostResponse(
                 headers={"Content-Type": "application/json"},
                 text='{"user_text":"hello"}',
                 payload={"user_text": "hello"},
             )) as post:
            svc._extract_user_text_via_webhook(p)
    finally:
        reset_request_language(token)
    assert post.call_args.kwargs["data"] == {"language": lang}


@pytest.mark.asyncio
async def test_user_language_reaches_webhook_through_to_thread(svc, tmp_path):
    """process_media 以 asyncio.to_thread 呼叫 webhook；語言要能跟著 context 過去。"""
    p = tmp_path / "a.m4a"
    p.write_bytes(b"x")
    token = set_request_language("th")
    try:
        with patch("app.services.media.mutimedia_processor.MEDIA_PARSE_WEBHOOK_URL", "https://x"), \
             patch("app.services.media.mutimedia_processor.requests.post", return_value=FakePostResponse(
                 headers={"Content-Type": "application/json"},
                 text='{"user_text":"hello"}',
                 payload={"user_text": "hello"},
             )) as post:
            await asyncio.to_thread(svc._extract_user_text_via_webhook, p)
    finally:
        reset_request_language(token)
    assert post.call_args.kwargs["data"] == {"language": "th"}


# faster-whisper 不認得台語；台語 STT 失敗退到 webhook 時要給國語提示。
def test_webhook_gets_zh_tw_hint_for_taiwanese_user(svc, tmp_path):
    p = tmp_path / "a.m4a"
    p.write_bytes(b"x")
    token = set_request_language("nan-TW")
    try:
        with patch("app.services.media.mutimedia_processor.MEDIA_PARSE_WEBHOOK_URL", "https://x"), \
             patch("app.services.media.mutimedia_processor.requests.post", return_value=FakePostResponse(
                 headers={"Content-Type": "application/json"},
                 text='{"user_text":"hello"}',
                 payload={"user_text": "hello"},
             )) as post:
            svc._extract_user_text_via_webhook(p)
    finally:
        reset_request_language(token)
    assert post.call_args.kwargs["data"] == {"language": "zh-TW"}


# ── 語言選台語的使用者，語音走 Taigi 台語 STT ─────────────────────────

RATE = 16_000


def _tone(seconds: float) -> bytes:
    n = int(seconds * RATE)
    return array.array(
        "h", (int(8000 * math.sin(2 * math.pi * 440 * i / RATE)) for i in range(n))
    ).tobytes()


def _silence(seconds: float) -> bytes:
    return bytes(int(seconds * RATE) * 2)


def _wav_seconds(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes() / w.getframerate()


class FakeTaigiClient:
    """回傳「N秒」，N 是收到那段音檔的長度，用來確認分段順序。"""

    def __init__(self, text=None, exc=None, available=True):
        self.text = text
        self.exc = exc
        self._available = available
        self.wavs: list[bytes] = []

    def available(self):
        return self._available

    def transcribe_wav(self, wav: bytes) -> str:
        self.wavs.append(wav)
        if self.exc is not None:
            raise self.exc
        if self.text is not None:
            return self.text
        return f"{round(_wav_seconds(wav))}秒"


class FakeTranscriber:
    """代替 Gemini 聽寫；記下收到的語言提示。"""

    def __init__(self, text="gemini 結果", exc=None, available=True):
        self.text = text
        self.exc = exc
        self._available = available
        self.languages: list[str] = []

    def available(self):
        return self._available

    async def transcribe(self, file_path, language):
        self.languages.append(language)
        if self.exc is not None:
            raise self.exc
        return self.text


async def _process_as(
    lang, media_type, taigi, tmp_path, pcm=None, webhook_text="whisper 結果", transcriber=None
):
    p = tmp_path / "voice.wav"
    p.write_bytes(speech_audio.pcm16_to_wav(pcm or _tone(2), RATE))
    svc = MediaProcessorService(
        taigi_client=taigi,
        transcriber=transcriber if transcriber is not None else FakeTranscriber(),
    )
    token = set_request_language(lang)
    try:
        with patch.object(svc, "_download_media_to_tmp", return_value=p), \
             patch.object(svc, "_extract_user_text_via_webhook", return_value=webhook_text) as webhook:
            out = await svc.process_media("mid", media_type, user_id="U1")
    finally:
        reset_request_language(token)
    return out, webhook


@pytest.mark.asyncio
async def test_taiwanese_audio_goes_to_taigi_as_16k_wav(tmp_path):
    taigi = FakeTaigiClient(text="阿公，你食飽未？")
    gemini = FakeTranscriber()

    out, webhook = await _process_as("nan-TW", "audio", taigi, tmp_path, transcriber=gemini)

    assert out == "阿公，你食飽未？"
    webhook.assert_not_called()
    assert gemini.languages == []
    assert len(taigi.wavs) == 1
    with wave.open(io.BytesIO(taigi.wavs[0])) as w:
        assert (w.getframerate(), w.getnchannels()) == (RATE, 1)


@pytest.mark.asyncio
async def test_long_taiwanese_audio_is_split_and_joined_in_order(tmp_path):
    taigi = FakeTaigiClient()
    pcm = _tone(18) + _silence(0.5) + _tone(21.5) + _silence(0.5) + _tone(10)

    out, _ = await _process_as("nan-TW", "audio", taigi, tmp_path, pcm=pcm)

    assert out == "18秒 22秒 10秒"
    assert all(_wav_seconds(w) <= speech_audio.MAX_STT_CHUNK_SECONDS for w in taigi.wavs)


@pytest.mark.asyncio
async def test_silent_taiwanese_audio_returns_no_content_text(tmp_path):
    out, webhook = await _process_as("nan-TW", "audio", FakeTaigiClient(text=""), tmp_path)

    assert out == NO_CONTENT_TEXT
    webhook.assert_not_called()


@pytest.mark.asyncio
async def test_taigi_failure_falls_back_to_gemini_with_zh_tw_hint(tmp_path):
    taigi = FakeTaigiClient(exc=RuntimeError("HTTP 500"))
    gemini = FakeTranscriber()

    out, webhook = await _process_as("nan-TW", "audio", taigi, tmp_path, transcriber=gemini)

    assert out == "gemini 結果"
    assert gemini.languages == ["zh-TW"]
    webhook.assert_not_called()


@pytest.mark.asyncio
async def test_missing_taigi_key_falls_back_without_calling_taigi(tmp_path):
    taigi = FakeTaigiClient(available=False)

    out, _ = await _process_as("nan-TW", "audio", taigi, tmp_path)

    assert out == "gemini 結果"
    assert taigi.wavs == []


@pytest.mark.asyncio
async def test_non_taiwanese_audio_skips_taigi(tmp_path):
    taigi = FakeTaigiClient()

    out, _ = await _process_as("zh-TW", "audio", taigi, tmp_path)

    assert out == "gemini 結果"
    assert taigi.wavs == []


@pytest.mark.asyncio
async def test_images_skip_speech_recognition(tmp_path):
    taigi = FakeTaigiClient()
    gemini = FakeTranscriber()

    out, webhook = await _process_as("nan-TW", "image", taigi, tmp_path, transcriber=gemini)

    assert out == "whisper 結果"
    webhook.assert_called_once()
    assert taigi.wavs == []
    assert gemini.languages == []


# ── 一般語言的語音先交給 Gemini，失敗才送 n8n／faster-whisper ─────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["zh-TW", "vi", "ja"])
async def test_audio_goes_to_gemini_with_user_language(tmp_path, lang):
    gemini = FakeTranscriber()

    out, webhook = await _process_as(lang, "audio", FakeTaigiClient(), tmp_path, transcriber=gemini)

    assert out == "gemini 結果"
    assert gemini.languages == [lang]
    webhook.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [RuntimeError("HTTP 503"), TimeoutError()])
async def test_gemini_failure_falls_back_to_webhook(tmp_path, exc):
    gemini = FakeTranscriber(exc=exc)

    out, webhook = await _process_as("en", "audio", FakeTaigiClient(), tmp_path, transcriber=gemini)

    assert out == "whisper 結果"
    webhook.assert_called_once()


@pytest.mark.asyncio
async def test_gemini_hearing_nothing_returns_no_content_text(tmp_path):
    gemini = FakeTranscriber(text="")

    out, webhook = await _process_as("zh-TW", "audio", FakeTaigiClient(), tmp_path, transcriber=gemini)

    assert out == NO_CONTENT_TEXT
    webhook.assert_not_called()


@pytest.mark.asyncio
async def test_missing_gemini_key_falls_back_to_webhook(tmp_path):
    gemini = FakeTranscriber(available=False)

    out, webhook = await _process_as("zh-TW", "audio", FakeTaigiClient(), tmp_path, transcriber=gemini)

    assert out == "whisper 結果"
    assert gemini.languages == []
    webhook.assert_called_once()


# --- 失敗要分類（LINE 進站流程） -----------------------------------------
#
# 以前所有失敗都回同一個「發生錯誤」字串，media_handler 再把它跟「沒辨識出
# 文字」混成一句。現在下載／n8n／STT 的問題、太大、不支援各自拋不同的例外。

from app.services.media.mutimedia_processor import (  # noqa: E402
    MAX_MEDIA_SIZE_BYTES,
    MediaProcessingError,
    MediaServiceUnavailableError,
    MediaTooLargeError,
    MediaUnsupportedError,
)
import requests as _requests  # noqa: E402


def _download_patches(svc, tmp_path, response):
    token_mgr = MagicMock()
    token_mgr.get_token.return_value = "t"
    return (
        patch("app.services.media.mutimedia_processor.TMP_DIR", tmp_path),
        patch("app.dependencies.get_line_token_manager", return_value=token_mgr),
        patch("app.services.media.mutimedia_processor.requests.get", **response),
    )


def test_content_length_over_limit_is_too_large(svc, tmp_path):
    big = MAX_MEDIA_SIZE_BYTES + 1
    p1, p2, p3 = _download_patches(
        svc, tmp_path,
        {"return_value": FakeGetResponse(headers={"Content-Type": "image/jpeg", "Content-Length": str(big)})},
    )
    with p1, p2, p3, pytest.raises(MediaTooLargeError) as exc_info:
        svc._download_media_to_tmp("mid", "image")
    assert exc_info.value.size_bytes == big
    assert exc_info.value.limit_bytes == MAX_MEDIA_SIZE_BYTES


def test_oversized_stream_without_content_length_is_too_large(svc, tmp_path):
    chunk = b"x" * (1024 * 1024)
    p1, p2, p3 = _download_patches(
        svc, tmp_path,
        {"return_value": FakeGetResponse(headers={"Content-Type": "image/jpeg"}, chunks=[chunk] * 11)},
    )
    with p1, p2, p3, pytest.raises(MediaTooLargeError):
        svc._download_media_to_tmp("mid", "image")


def test_mime_mismatch_is_unsupported(svc, tmp_path):
    p1, p2, p3 = _download_patches(
        svc, tmp_path,
        {"return_value": FakeGetResponse(headers={"Content-Type": "text/html", "Content-Length": "3"})},
    )
    with p1, p2, p3, pytest.raises(MediaUnsupportedError):
        svc._download_media_to_tmp("mid", "image")


def test_line_download_failure_is_service_unavailable(svc, tmp_path):
    p1, p2, p3 = _download_patches(
        svc, tmp_path, {"side_effect": _requests.ConnectionError("LINE down")}
    )
    with p1, p2, p3, pytest.raises(MediaServiceUnavailableError):
        svc._download_media_to_tmp("mid", "image")


def test_webhook_unreachable_is_service_unavailable(svc, tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"abc")
    with patch("app.services.media.mutimedia_processor.MEDIA_PARSE_WEBHOOK_URL", "http://n8n/x"), \
         patch("app.services.media.mutimedia_processor.requests.post", side_effect=_requests.Timeout("slow")):
        with pytest.raises(MediaServiceUnavailableError):
            svc._extract_user_text_via_webhook(p)


@pytest.mark.parametrize(
    "response",
    [
        FakePostResponse(headers={"Content-Type": "application/json"}, text=""),
        FakePostResponse(
            headers={"Content-Type": "application/json"},
            text="<html>",
            payload=_requests.exceptions.JSONDecodeError("bad", "<html>", 0),
        ),
    ],
)
def test_webhook_garbage_is_service_unavailable_not_empty_transcript(svc, tmp_path, response):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"abc")
    with patch("app.services.media.mutimedia_processor.MEDIA_PARSE_WEBHOOK_URL", "http://n8n/x"), \
         patch("app.services.media.mutimedia_processor.requests.post", return_value=response):
        with pytest.raises(MediaServiceUnavailableError):
            svc._extract_user_text_via_webhook(p)


@pytest.mark.asyncio
async def test_process_media_propagates_typed_errors_and_cleans_up(svc, tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"abc")
    with patch.object(svc, "_download_media_to_tmp", return_value=p), \
         patch.object(svc, "_extract_user_text_via_webhook", side_effect=MediaServiceUnavailableError("n8n")):
        with pytest.raises(MediaServiceUnavailableError):
            await svc.process_media("mid", "image", user_id="U1")
    assert not p.exists()


@pytest.mark.asyncio
async def test_process_media_wraps_unexpected_errors_as_service_unavailable(svc, tmp_path):
    with patch.object(svc, "_download_media_to_tmp", side_effect=OSError("disk full")):
        with pytest.raises(MediaServiceUnavailableError) as exc_info:
            await svc.process_media("mid", "image", user_id="U1")
    assert isinstance(exc_info.value, MediaProcessingError)
    assert isinstance(exc_info.value.__cause__, OSError)
