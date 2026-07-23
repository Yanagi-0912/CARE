from unittest.mock import MagicMock, call

import pytest
import requests

from app.services.liff.line_id_token_service import LineIdTokenService


def test_verify_returns_payload_on_success():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"sub": "U123", "name": "Amy", "picture": "https://pic.jpg"}

    service = LineIdTokenService()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "app.services.liff.line_id_token_service.requests.post",
            MagicMock(return_value=response),
        )
        payload = service.verify(id_token="id-token", client_id="client-id")

    assert payload == {"sub": "U123", "name": "Amy", "picture": "https://pic.jpg"}


def test_verify_raises_value_error_on_non_200():
    response = MagicMock()
    response.status_code = 400
    response.text = "invalid"

    service = LineIdTokenService()

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "app.services.liff.line_id_token_service.requests.post",
            MagicMock(return_value=response),
        )
        with pytest.raises(ValueError):
            service.verify(id_token="bad-token", client_id="client-id")


def test_verify_retries_on_timeout_then_succeeds():
    success = MagicMock()
    success.status_code = 200
    success.json.return_value = {"sub": "U123"}

    post = MagicMock(
        side_effect=[
            requests.ConnectTimeout("timed out"),
            success,
        ]
    )
    service = LineIdTokenService(retry_backoff_seconds=0)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "app.services.liff.line_id_token_service.requests.post",
            post,
        )
        payload = service.verify(id_token="id-token", client_id="client-id")

    assert payload == {"sub": "U123"}
    assert post.call_count == 2


def test_verify_raises_after_all_retries_fail():
    post = MagicMock(side_effect=requests.ConnectTimeout("timed out"))
    service = LineIdTokenService(max_attempts=3, retry_backoff_seconds=0)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "app.services.liff.line_id_token_service.requests.post",
            post,
        )
        with pytest.raises(requests.ConnectTimeout):
            service.verify(id_token="id-token", client_id="client-id")

    assert post.call_count == 3
    assert post.call_args_list == [
        call(
            "https://api.line.me/oauth2/v2.1/verify",
            data={"id_token": "id-token", "client_id": "client-id"},
            timeout=10,
        )
    ] * 3
