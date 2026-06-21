from unittest.mock import patch
from pathlib import Path
from fastapi.testclient import TestClient
from linebot.v3.exceptions import InvalidSignatureError
from app.main import app

client = TestClient(app)


def test_callback_missing_signature_returns_400():
    response = client.post(
        "/line/callback",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    detail = response.json().get("detail", "")
    assert "missing" in detail.lower()


@patch("app.routers.line.webhook.parser")  # 用 patch 模擬 parser 物件
def test_callback_invalid_signature_returns_400(mock_parser):
    mock_parser.parse.side_effect = InvalidSignatureError(
        "invalid"
    )  # 模擬 parser.parse() 方法引發 InvalidSignatureError 異常
    response = client.post(
        "/line/callback",
        content=b'{"events":[]}',
        headers={
            "Content-Type": "application/json",
            "X-Line-Signature": "dummy_signature",
        },
    )
    assert response.status_code == 400
    assert "signature" in response.json().get("detail", "").lower()


@patch("app.routers.line.webhook.parser")
def test_callback_valid_request_returns_200(mock_parser):
    mock_parser.parse.return_value = []
    response = client.post(
        "/line/callback",
        content=b'{"events":[]}',
        headers={
            "Content-Type": "application/json",
            "X-Line-Signature": "valid_signature",
        },
    )
    assert response.status_code == 200
    assert response.text == '"OK"'


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
