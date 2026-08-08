import json

import pytest

from resources.flex_messages.official_site_flex_message import (
    generate_official_site_flex_message,
)


def _uri_actions(payload: dict) -> list[dict]:
    actions: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            action = node.get("action")
            if isinstance(action, dict) and action.get("type") == "uri":
                actions.append(action)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return actions


def test_flex_single_button_prefers_liff():
    # 兩個 URL 對使用者是同一個目的地，因此卡片只保留一顆按鈕
    payload = generate_official_site_flex_message(
        "https://liff.line.me/abc",
        "https://care.example.com",
    )

    assert payload["type"] == "flex"
    assert payload["altText"] == "CARE 官方入口"
    assert payload["contents"]["type"] == "bubble"
    assert payload["contents"]["body"]["type"] == "box"

    actions = _uri_actions(payload)
    assert len(actions) == 1
    assert actions[0]["label"] == "開啟 CARE"
    assert actions[0]["uri"] == "https://liff.line.me/abc"


def test_flex_only_liff_url():
    payload = generate_official_site_flex_message(
        "https://liff.line.me/abc",
        "",
    )
    actions = _uri_actions(payload)
    assert len(actions) == 1
    assert actions[0]["uri"] == "https://liff.line.me/abc"


def test_flex_falls_back_to_public_base_url():
    payload = generate_official_site_flex_message(
        "",
        "https://care.example.com",
    )
    actions = _uri_actions(payload)
    assert len(actions) == 1
    assert actions[0]["uri"] == "https://care.example.com"


def test_flex_strips_whitespace_urls():
    payload = generate_official_site_flex_message(
        "  https://liff.line.me/abc  ",
        "  https://care.example.com  ",
    )
    actions = _uri_actions(payload)
    assert actions[0]["uri"] == "https://liff.line.me/abc"


def test_flex_respects_language_setting():
    payload = generate_official_site_flex_message(
        "https://liff.line.me/abc", "", language="en"
    )
    actions = _uri_actions(payload)
    assert actions[0]["label"] == "Open CARE"
    assert payload["altText"] == "CARE official entry"


def test_flex_respects_font_size_setting():
    normal = generate_official_site_flex_message(
        "https://liff.line.me/abc", "", font_size="normal"
    )
    xlarge = generate_official_site_flex_message(
        "https://liff.line.me/abc", "", font_size="xlarge"
    )
    # body 的第二個元素是標題文字
    assert normal["contents"]["body"]["contents"][1]["size"] == "xl"
    assert xlarge["contents"]["body"]["contents"][1]["size"] == "4xl"


def test_flex_raises_when_both_urls_empty():
    with pytest.raises(ValueError, match="At least one non-empty URL"):
        generate_official_site_flex_message("", "")

    with pytest.raises(ValueError, match="At least one non-empty URL"):
        generate_official_site_flex_message("   ", "  ")


def test_flex_serializes_as_json():
    payload = generate_official_site_flex_message(
        "https://liff.line.me/abc",
        "https://care.example.com",
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    parsed = json.loads(serialized)
    assert parsed["type"] == "flex"
