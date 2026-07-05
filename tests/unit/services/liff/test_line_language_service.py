from unittest.mock import MagicMock

import pytest
import requests

from app.services.liff.line_language_service import LineLanguageService


def test_get_language_returns_language_field():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "userId": "U123",
        "displayName": "Amy",
        "pictureUrl": "https://line.example/pic.jpg",
        "language": "zh-TW",
    }

    service = LineLanguageService(get_access_token=lambda: "token")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "app.services.liff.line_language_service.requests.get",
            MagicMock(return_value=response),
        )
        language = service.get_language("U123")

    assert language == "zh-TW"


def test_get_language_returns_none_when_api_fails():
    service = LineLanguageService(get_access_token=lambda: "token")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "app.services.liff.line_language_service.requests.get",
            MagicMock(side_effect=requests.RequestException("network down")),
        )
        language = service.get_language("U123")

    assert language is None


def test_get_language_returns_none_when_token_unavailable():
    service = LineLanguageService(
        get_access_token=lambda: (_ for _ in ()).throw(ValueError("no token"))
    )

    language = service.get_language("U123")

    assert language is None


def test_get_language_returns_none_when_status_not_200():
    response = MagicMock()
    response.status_code = 404
    response.text = "not found"

    service = LineLanguageService(get_access_token=lambda: "token")

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "app.services.liff.line_language_service.requests.get",
            MagicMock(return_value=response),
        )
        language = service.get_language("U123")

    assert language is None
