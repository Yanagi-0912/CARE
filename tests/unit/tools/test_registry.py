from app.tools.registry import get_all_tools


def _tool_names(tools: list) -> set[str]:
    if not tools:
        return set()
    return {getattr(t, "name", str(t)) for t in tools}


def test_get_all_tools_includes_rag_and_web_when_enabled():
    tools = get_all_tools(include_rag_tool=True)
    names = _tool_names(tools)
    assert "get_rag_answer" in names
    assert "search_public_web" in names
    assert "find_nearby_hospitals" in names
    assert "lookup_medical_facility" in names
    assert "request_location_quick_reply" in names


def test_get_all_tools_excludes_rag_and_web_when_disabled():
    tools = get_all_tools(include_rag_tool=False)
    names = _tool_names(tools)
    assert "get_rag_answer" not in names
    assert "search_public_web" not in names
    assert "find_nearby_hospitals" in names
    assert "lookup_medical_facility" in names
    assert "request_location_quick_reply" in names


def test_get_all_tools_can_toggle_web_independently():
    tools = get_all_tools(include_rag_tool=True, include_web_tool=False)
    names = _tool_names(tools)
    assert "get_rag_answer" in names
    assert "search_public_web" not in names
