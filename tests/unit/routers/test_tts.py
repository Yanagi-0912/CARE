from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_tts_audio_route_serves_only_generated_mp3():
    audio_file = Path("app_data") / "tmp" / "tts_route_test.mp3"
    private_file = Path("app_data") / "tmp" / "private.mp3"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"mp3")
    private_file.write_bytes(b"private")

    try:
        response = client.get("/tts/tts_route_test.mp3")
        assert response.status_code == 200
        assert response.content == b"mp3"
        assert response.headers["content-type"].startswith("audio/mpeg")

        blocked = client.get("/tts/private.mp3")
        assert blocked.status_code == 404
    finally:
        audio_file.unlink(missing_ok=True)
        private_file.unlink(missing_ok=True)
