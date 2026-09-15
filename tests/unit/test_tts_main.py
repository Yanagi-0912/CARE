import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app import tts_main
from app.core.config import settings
from app.core.request_context import get_request_id

REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeTTS:
    """DI 用的假 TTSService，記錄呼叫參數與當下的 rid。"""

    def __init__(self, output: str = "", exc: Exception | None = None):
        self.output = output
        self.exc = exc
        self.calls: list[dict] = []

    async def synthesize(
        self, text, language="zh-TW", voice_rate="normal", voice_gender="female"
    ):
        self.calls.append(
            {
                "text": text,
                "language": language,
                "voice_rate": voice_rate,
                "voice_gender": voice_gender,
                "rid": get_request_id(),
            }
        )
        if self.exc is not None:
            raise self.exc
        return b"mp3", self.output, 2345


def test_synthesize_returns_a_url_that_this_service_serves(monkeypatch):
    audio_file = Path("app_data") / "tmp" / "tts_main_test.mp3"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"mp3")
    fake = FakeTTS(output=str(audio_file))
    monkeypatch.setattr(tts_main, "_tts_service", fake)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://care.example")
    monkeypatch.setattr(settings, "TTS_AUDIO_URL_PATH", "/tts")
    client = TestClient(tts_main.app)

    try:
        response = client.post(
            "/internal/synthesize",
            json={
                "text": "記得吃藥",
                "language": "nan-TW",
                "voice_rate": "slow",
                "voice_gender": "male",
            },
            headers={"X-Request-ID": "abc12345"},
        )
        assert response.status_code == 200
        assert response.json() == {
            "audio_url": "https://care.example/tts/tts_main_test.mp3",
            "duration_ms": 2345,
        }
        assert fake.calls == [
            {
                "text": "記得吃藥",
                "language": "nan-TW",
                "voice_rate": "slow",
                "voice_gender": "male",
                "rid": "abc12345",
            }
        ]

        served = client.get("/tts/tts_main_test.mp3")
        assert served.status_code == 200
        assert served.content == b"mp3"
    finally:
        audio_file.unlink(missing_ok=True)


def test_synthesis_failure_is_502(monkeypatch):
    monkeypatch.setattr(
        tts_main, "_tts_service", FakeTTS(exc=RuntimeError("edge-tts unavailable"))
    )

    response = TestClient(tts_main.app).post(
        "/internal/synthesize", json={"text": "記得吃藥"}
    )

    assert response.status_code == 502


def test_missing_public_base_url_is_503(monkeypatch):
    audio_file = Path("app_data") / "tmp" / "tts_main_no_base_url.mp3"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"mp3")
    monkeypatch.setattr(tts_main, "_tts_service", FakeTTS(output=str(audio_file)))
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "")

    try:
        response = TestClient(tts_main.app).post(
            "/internal/synthesize", json={"text": "記得吃藥"}
        )
        assert response.status_code == 503
    finally:
        audio_file.unlink(missing_ok=True)


def test_health():
    response = TestClient(tts_main.app).get("/health")

    assert response.status_code == 200


def test_does_not_load_the_backend_app():
    """app.dependencies 一載入就建藥證庫索引（scheduler 用同一個 image 閒置 467Mi）。

    care-tts 的記憶體上限是照「只載入語音模組」量出來的；誰在這條 import 鏈加了
    backend 的東西，pod 會在啟動時被 OOMKilled。
    """
    code = (
        "import sys, app.tts_main\n"
        "loaded = [m for m in ('app.main', 'app.dependencies') if m in sys.modules]\n"
        "print(loaded)\n"
        "sys.exit(1 if loaded else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
