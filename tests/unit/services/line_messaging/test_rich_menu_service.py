import json
from unittest.mock import MagicMock

import pytest
import requests

from app.services.line_messaging.rich_menu_service import (
    RichMenuService,
    load_rich_menu_ids,
)


MENU_IDS = {
    "zh-TW": "richmenu-zh",
    "en": "richmenu-en",
    "id": "richmenu-id",
    "vi": "richmenu-vi",
    "th": "richmenu-th",
    "ja": "richmenu-ja",
}


@pytest.fixture
def service() -> RichMenuService:
    return RichMenuService(
        get_access_token=lambda: "test-token",
        menu_ids=MENU_IDS,
    )


def test_resolve_menu_id_known_language(service: RichMenuService):
    assert service.resolve_menu_id("en") == "richmenu-en"
    assert service.resolve_menu_id("zh-TW") == "richmenu-zh"


def test_resolve_menu_id_unknown_language_falls_back_to_zh_tw(service: RichMenuService):
    assert service.resolve_menu_id("xx") == "richmenu-zh"


def test_resolve_menu_id_missing_id_returns_none():
    service = RichMenuService(
        get_access_token=lambda: "test-token",
        menu_ids={"zh-TW": "richmenu-zh"},
    )
    assert service.resolve_menu_id("en") is None


def test_link_user_menu_success():
    captured: dict = {}

    def fake_post(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        response = MagicMock()
        response.status_code = 200
        return response

    service = RichMenuService(
        get_access_token=lambda: "test-token",
        menu_ids=MENU_IDS,
        http_post=fake_post,
    )

    assert service.link_user_menu("U123", "en") is True
    assert captured["url"] == "https://api.line.me/v2/bot/user/U123/richmenu/richmenu-en"
    assert captured["headers"] == {"Authorization": "Bearer test-token"}


def test_link_user_menu_returns_false_when_missing_menu_id():
    service = RichMenuService(
        get_access_token=lambda: "test-token",
        menu_ids={"zh-TW": "richmenu-zh"},
        http_post=MagicMock(),
    )

    assert service.link_user_menu("U123", "en") is False


def test_link_user_menu_returns_false_when_not_200():
    def fake_post(url, headers=None, timeout=None):
        response = MagicMock()
        response.status_code = 404
        response.text = "not found"
        return response

    service = RichMenuService(
        get_access_token=lambda: "test-token",
        menu_ids=MENU_IDS,
        http_post=fake_post,
    )

    assert service.link_user_menu("U123", "en") is False


def test_link_user_menu_returns_false_on_request_exception():
    def fake_post(url, headers=None, timeout=None):
        raise requests.RequestException("network down")

    service = RichMenuService(
        get_access_token=lambda: "test-token",
        menu_ids=MENU_IDS,
        http_post=fake_post,
    )

    assert service.link_user_menu("U123", "en") is False


def test_load_rich_menu_ids_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "app.services.line_messaging.rich_menu_service.settings.RICH_MENU_IDS_JSON",
        json.dumps({"zh-TW": "richmenu-env", "en": "richmenu-env-en"}),
    )

    ids = load_rich_menu_ids()

    assert ids == {"zh-TW": "richmenu-env", "en": "richmenu-env-en"}


def test_load_rich_menu_ids_from_file(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(
        "app.services.line_messaging.rich_menu_service.settings.RICH_MENU_IDS_JSON",
        "",
    )

    ids_file = tmp_path / "rich_menu_ids.json"
    ids_file.write_text(
        json.dumps({"zh-TW": "richmenu-file", "en": "richmenu-file-en"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.line_messaging.rich_menu_service.RICH_MENU_IDS_PATH",
        ids_file,
    )

    ids = load_rich_menu_ids()

    assert ids == {"zh-TW": "richmenu-file", "en": "richmenu-file-en"}


def test_load_rich_menu_ids_returns_empty_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(
        "app.services.line_messaging.rich_menu_service.settings.RICH_MENU_IDS_JSON",
        "",
    )
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(
        "app.services.line_messaging.rich_menu_service.RICH_MENU_IDS_PATH",
        missing,
    )

    assert load_rich_menu_ids() == {}
