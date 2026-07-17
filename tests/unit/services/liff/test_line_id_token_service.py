from unittest.mock import MagicMock

import pytest

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
