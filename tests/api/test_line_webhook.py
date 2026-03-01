"""LINE Webhook API 測試：簽名驗證與回傳狀態."""
from unittest.mock import AsyncMock, patch

import pytest
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
    assert "missing" in detail.lower() or "signature" in detail.lower()


@patch("app.routers.line.webhook.parser")
def test_callback_invalid_signature_returns_400(mock_parser):
    mock_parser.parse.side_effect = InvalidSignatureError("invalid")
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
