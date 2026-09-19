"""Taigi 台語 TTS／STT 客戶端：請求形狀與錯誤分流。不打真的 API。"""

from unittest.mock import MagicMock

import pytest

from app.services.speech import taigi_client as taigi_module
from app.services.speech.taigi_client import (
    STT_PATH,
    STT_TIMEOUT_SECONDS,
    TTS_PATH,
    TTS_TIMEOUT_SECONDS,
    TaigiClient,
    TaigiError,
)


def _response(status=200, content_type="audio/wav", content=b"RIFF", payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.headers = {"Content-Type": content_type}
    r.content = content
    r.text = text
    if isinstance(payload, Exception):
        r.json.side_effect = payload
    else:
        r.json.return_value = payload
    return r


def test_available_requires_key():
    assert not TaigiClient(api_key="  ").available()
    assert TaigiClient(api_key="k").available()


def test_synthesize_posts_v6_with_key_voice_and_speed(monkeypatch):
    post = MagicMock(return_value=_response(content=b"RIFFwav"))
    monkeypatch.setattr(taigi_module.requests, "post", post)

    out = TaigiClient(api_key="k", base_url="https://t.example/").synthesize_wav(
        "阿公", voice_label="normal_m2", speed=0.9
    )

    assert out == b"RIFFwav"
    post.assert_called_once_with(
        "https://t.example" + TTS_PATH,
        headers={"x-api-key": "k"},
        json={"text": "阿公", "voice_label": "normal_m2", "speed": 0.9, "user": ""},
        timeout=TTS_TIMEOUT_SECONDS,
    )


# 錯誤與「靜音對象」都回 JSON；HTTP 200 也不代表拿到的是音檔。
@pytest.mark.parametrize(
    "status,content_type",
    [(200, "application/json"), (403, "application/json"), (502, "text/html")],
)
def test_synthesize_rejects_non_audio_response(monkeypatch, status, content_type):
    monkeypatch.setattr(
        taigi_module.requests,
        "post",
        MagicMock(return_value=_response(status=status, content_type=content_type, text="{}")),
    )
    with pytest.raises(TaigiError):
        TaigiClient(api_key="k").synthesize_wav("阿公")


def test_transcribe_posts_wav_and_returns_best(monkeypatch):
    post = MagicMock(
        return_value=_response(
            content_type="application/json", payload={"best": " 阿公，你食飽未？ ", "duration": 3}
        )
    )
    monkeypatch.setattr(taigi_module.requests, "post", post)

    text = TaigiClient(api_key="k", base_url="https://t.example").transcribe_wav(b"RIFF")

    assert text == "阿公，你食飽未？"
    args, kwargs = post.call_args
    assert args[0] == "https://t.example" + STT_PATH
    assert kwargs["headers"] == {"x-api-key": "k"}
    assert kwargs["files"] == {"voiceFile": ("audio.wav", b"RIFF", "audio/wav")}
    assert kwargs["timeout"] == STT_TIMEOUT_SECONDS


def test_transcribe_silence_returns_empty_string(monkeypatch):
    monkeypatch.setattr(
        taigi_module.requests,
        "post",
        MagicMock(return_value=_response(content_type="application/json", payload={"best": None})),
    )
    assert TaigiClient(api_key="k").transcribe_wav(b"RIFF") == ""


# 2026-09-14 實測送 m4a 時回的就是這個。
def test_transcribe_http_error_raises(monkeypatch):
    monkeypatch.setattr(
        taigi_module.requests,
        "post",
        MagicMock(
            return_value=_response(
                status=500,
                content_type="application/json",
                text='{"error":"Format not recognised."}',
            )
        ),
    )
    with pytest.raises(TaigiError, match="500"):
        TaigiClient(api_key="k").transcribe_wav(b"x")


def test_transcribe_non_json_raises(monkeypatch):
    monkeypatch.setattr(
        taigi_module.requests,
        "post",
        MagicMock(
            return_value=_response(
                content_type="text/html", payload=ValueError("no json"), text="<html>"
            )
        ),
    )
    with pytest.raises(TaigiError):
        TaigiClient(api_key="k").transcribe_wav(b"x")


# ── 暫時性錯誤重試一次，429 不重試（理由見 taigi_client.STT_RETRY_STATUS）──────


def test_transcribe_retries_once_on_server_error(monkeypatch):
    post = MagicMock(
        side_effect=[_response(status=503, text="upstream down"),
                     _response(payload={"best": "阿公你食飽未"})]
    )
    monkeypatch.setattr(taigi_module.requests, "post", post)
    monkeypatch.setattr(taigi_module.time, "sleep", lambda _s: None)

    out = TaigiClient(api_key="k").transcribe_wav(b"RIFF")

    assert out == "阿公你食飽未"
    assert post.call_count == 2


def test_transcribe_does_not_retry_on_429(monkeypatch):
    """速率限制的窗口是分鐘級，馬上重送必然再撞一次，白白多等。"""
    post = MagicMock(return_value=_response(status=429, text="アクセスが頻繁すぎます"))
    monkeypatch.setattr(taigi_module.requests, "post", post)

    with pytest.raises(TaigiError, match="429"):
        TaigiClient(api_key="k").transcribe_wav(b"RIFF")

    assert post.call_count == 1


def test_transcribe_gives_up_after_one_retry(monkeypatch):
    post = MagicMock(return_value=_response(status=502, text="bad gateway"))
    monkeypatch.setattr(taigi_module.requests, "post", post)
    monkeypatch.setattr(taigi_module.time, "sleep", lambda _s: None)

    with pytest.raises(TaigiError, match="502"):
        TaigiClient(api_key="k").transcribe_wav(b"RIFF")

    assert post.call_count == 2
