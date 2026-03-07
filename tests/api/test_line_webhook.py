from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


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
