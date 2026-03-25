from app.tools.registry import get_all_gemini_tools


def _tool_names(tools: list) -> set[str]:
    if not tools:
        return set()
    decls = tools[0].get("functionDeclarations", [])
    return {d["name"] for d in decls}


def test_get_all_gemini_tools_includes_rag_when_enabled():
    tools = get_all_gemini_tools(include_rag_tool=True)
    names = _tool_names(tools)
    assert "get_rag_answer" in names
    assert "request_location" in names


def test_get_all_gemini_tools_excludes_rag_when_disabled():
    tools = get_all_gemini_tools(include_rag_tool=False)
    names = _tool_names(tools)
    assert "get_rag_answer" not in names
    assert "request_location" in names
