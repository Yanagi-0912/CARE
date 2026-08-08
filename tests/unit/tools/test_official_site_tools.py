import json

import pytest

from app.tools import official_site_tools as tools
from app.tools.registry import get_all_tools


@pytest.fixture(autouse=True)
def reset_tool_state():
    tools.configure_official_site_tool(liff_url="", public_base_url="")
    yield
    tools.configure_official_site_tool(liff_url="", public_base_url="")


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


@pytest.mark.asyncio
async def test_open_official_site_prefers_liff_when_both_urls_set():
    # LIFF 與官網對使用者是同一個目的地，卡片只出一顆按鈕並優先採用 LIFF
    tools.configure_official_site_tool(
        liff_url="https://liff.line.me/abc",
        public_base_url="https://care.example.com",
    )
    result = await tools.open_official_site.ainvoke({})

    payload = json.loads(result)
    assert payload["type"] == "flex"
    actions = _uri_actions(payload)
    assert len(actions) == 1
    assert actions[0]["uri"] == "https://liff.line.me/abc"


@pytest.mark.asyncio
async def test_open_official_site_returns_flex_json_when_only_liff_set():
    tools.configure_official_site_tool(
        liff_url="https://liff.line.me/abc",
        public_base_url="",
    )
    result = await tools.open_official_site.ainvoke({})
    payload = json.loads(result)
    actions = _uri_actions(payload)
    assert len(actions) == 1
    assert actions[0]["uri"] == "https://liff.line.me/abc"


@pytest.mark.asyncio
async def test_open_official_site_falls_back_to_public_url():
    tools.configure_official_site_tool(
        liff_url="",
        public_base_url="https://care.example.com",
    )
    result = await tools.open_official_site.ainvoke({})
    payload = json.loads(result)
    actions = _uri_actions(payload)
    assert len(actions) == 1
    assert actions[0]["uri"] == "https://care.example.com"


@pytest.mark.asyncio
async def test_open_official_site_returns_plain_text_when_both_empty():
    result = await tools.open_official_site.ainvoke({})
    assert result == tools.EMPTY_URLS_MESSAGE
    with pytest.raises(json.JSONDecodeError):
        json.loads(result)


def test_open_official_site_registered_in_get_all_tools():
    for include_rag in (True, False):
        names = {getattr(t, "name", str(t)) for t in get_all_tools(include_rag_tool=include_rag)}
        assert "open_official_site" in names
