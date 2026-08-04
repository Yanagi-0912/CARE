import json
from unittest.mock import MagicMock

import scripts.setup_rich_menu as setup_rich_menu
from scripts.setup_rich_menu import (
    cleanup_old_rich_menus,
    delete_rich_menu,
    read_existing_menu_ids,
    stale_menu_ids,
)


def _response(status_code: int, text: str = "") -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    return response


# ── read_existing_menu_ids ─────────────────────────────────────────


def test_read_existing_menu_ids_returns_saved_ids(tmp_path, monkeypatch):
    path = tmp_path / "rich_menu_ids.json"
    path.write_text(
        json.dumps({"zh-TW": "richmenu-old-zh", "en": "richmenu-old-en"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_rich_menu, "RICH_MENU_IDS_PATH", str(path))

    assert read_existing_menu_ids() == {
        "zh-TW": "richmenu-old-zh",
        "en": "richmenu-old-en",
    }


def test_read_existing_menu_ids_returns_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        setup_rich_menu, "RICH_MENU_IDS_PATH", str(tmp_path / "nope.json")
    )

    assert read_existing_menu_ids() == {}


def test_read_existing_menu_ids_tolerates_broken_json(tmp_path, monkeypatch, capsys):
    path = tmp_path / "rich_menu_ids.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(setup_rich_menu, "RICH_MENU_IDS_PATH", str(path))

    assert read_existing_menu_ids() == {}
    assert "略過舊選單清理" in capsys.readouterr().out


def test_read_existing_menu_ids_ignores_non_object_json(tmp_path, monkeypatch, capsys):
    path = tmp_path / "rich_menu_ids.json"
    path.write_text(json.dumps(["richmenu-old"]), encoding="utf-8")
    monkeypatch.setattr(setup_rich_menu, "RICH_MENU_IDS_PATH", str(path))

    assert read_existing_menu_ids() == {}
    assert "略過舊選單清理" in capsys.readouterr().out


def test_read_existing_menu_ids_drops_empty_values(tmp_path, monkeypatch):
    path = tmp_path / "rich_menu_ids.json"
    path.write_text(json.dumps({"zh-TW": "richmenu-a", "en": ""}), encoding="utf-8")
    monkeypatch.setattr(setup_rich_menu, "RICH_MENU_IDS_PATH", str(path))

    assert read_existing_menu_ids() == {"zh-TW": "richmenu-a"}


# ── stale_menu_ids ─────────────────────────────────────────────────


def test_stale_menu_ids_excludes_newly_created_ids():
    old = {"zh-TW": "richmenu-old", "en": "richmenu-keep"}
    new = {"zh-TW": "richmenu-new", "en": "richmenu-keep"}

    assert stale_menu_ids(old, new) == ["richmenu-old"]


def test_stale_menu_ids_dedupes_and_sorts():
    old = {"zh-TW": "richmenu-b", "en": "richmenu-b", "ja": "richmenu-a"}

    assert stale_menu_ids(old, {}) == ["richmenu-a", "richmenu-b"]


def test_stale_menu_ids_empty_when_no_previous_run():
    assert stale_menu_ids({}, {"zh-TW": "richmenu-new"}) == []


# ── delete_rich_menu ───────────────────────────────────────────────


def test_delete_rich_menu_sends_delete_with_bearer_token(monkeypatch):
    requests_mock = MagicMock()
    requests_mock.delete.return_value = _response(200)
    monkeypatch.setattr(setup_rich_menu, "requests", requests_mock)

    assert delete_rich_menu("token-123", "richmenu-old") is True
    requests_mock.delete.assert_called_once_with(
        "https://api.line.me/v2/bot/richmenu/richmenu-old",
        headers={"Authorization": "Bearer token-123"},
    )


def test_delete_rich_menu_treats_404_as_already_gone(monkeypatch):
    requests_mock = MagicMock()
    requests_mock.delete.return_value = _response(404, "not found")
    monkeypatch.setattr(setup_rich_menu, "requests", requests_mock)

    assert delete_rich_menu("token-123", "richmenu-gone") is True


def test_delete_rich_menu_reports_failure(monkeypatch, capsys):
    requests_mock = MagicMock()
    requests_mock.delete.return_value = _response(500, "boom")
    monkeypatch.setattr(setup_rich_menu, "requests", requests_mock)

    assert delete_rich_menu("token-123", "richmenu-stuck") is False
    assert "刪除失敗" in capsys.readouterr().out


# ── cleanup_old_rich_menus ─────────────────────────────────────────


def test_cleanup_deletes_every_stale_menu(monkeypatch, capsys):
    requests_mock = MagicMock()
    requests_mock.delete.return_value = _response(200)
    monkeypatch.setattr(setup_rich_menu, "requests", requests_mock)

    cleanup_old_rich_menus(
        "token-123",
        {"zh-TW": "richmenu-old-zh", "en": "richmenu-old-en"},
        {"zh-TW": "richmenu-new-zh", "en": "richmenu-new-en"},
    )

    deleted = {call.args[0] for call in requests_mock.delete.call_args_list}
    assert deleted == {
        "https://api.line.me/v2/bot/richmenu/richmenu-old-en",
        "https://api.line.me/v2/bot/richmenu/richmenu-old-zh",
    }
    assert "已清理 2 個舊 Rich Menu" in capsys.readouterr().out


def test_cleanup_never_deletes_the_menus_just_created(monkeypatch):
    """本次新建的 ID 絕不能被清理掉 —— 否則會刪掉剛設為預設的選單。"""
    requests_mock = MagicMock()
    requests_mock.delete.return_value = _response(200)
    monkeypatch.setattr(setup_rich_menu, "requests", requests_mock)

    new_ids = {"zh-TW": "richmenu-new-zh"}
    cleanup_old_rich_menus("token-123", new_ids, new_ids)

    requests_mock.delete.assert_not_called()


def test_cleanup_skips_api_call_when_nothing_to_clean(monkeypatch, capsys):
    requests_mock = MagicMock()
    monkeypatch.setattr(setup_rich_menu, "requests", requests_mock)

    cleanup_old_rich_menus("token-123", {}, {"zh-TW": "richmenu-new"})

    requests_mock.delete.assert_not_called()
    assert "沒有需要清理的舊 Rich Menu" in capsys.readouterr().out


def test_cleanup_warns_about_leftovers_without_raising(monkeypatch, capsys):
    requests_mock = MagicMock()
    requests_mock.delete.side_effect = [_response(200), _response(500, "boom")]
    monkeypatch.setattr(setup_rich_menu, "requests", requests_mock)

    cleanup_old_rich_menus(
        "token-123",
        {"zh-TW": "richmenu-aaa", "en": "richmenu-bbb"},
        {},
    )

    out = capsys.readouterr().out
    assert "未能刪除" in out
    assert "richmenu-bbb" in out
