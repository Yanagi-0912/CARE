import pytest

from app.tools.claim_tools import configure_claim_tool
from app.tools.registry import get_all_tools


def _tool_names(tools: list) -> set[str]:
    if not tools:
        return set()
    return {getattr(t, "name", str(t)) for t in tools}


@pytest.fixture(autouse=True)
def reset_claim_tool_state():
    """verify_claim 是否出現在工具清單取決於 claim_tools 是否已被
    configure_claim_tool 注入服務；重置狀態避免與其他測試檔案互相污染
    （這個模組層級全域也被 test_claim_tools.py 使用）。"""
    configure_claim_tool(None)
    yield
    configure_claim_tool(None)


def test_get_all_tools_includes_rag_when_enabled():
    configure_claim_tool(object())  # 任意非 None 值即代表「已配置」
    tools = get_all_tools(include_rag_tool=True)
    names = _tool_names(tools)
    assert "get_rag_answer" in names
    assert "answer_from_uploaded_document" in names
    assert "submit_knowledge_report" in names
    assert "search_public_web" not in names
    assert "find_nearby_hospitals" in names
    assert "lookup_medical_facility" in names
    assert "request_location_quick_reply" in names
    assert "open_official_site" in names
    assert "verify_claim" in names


def test_get_all_tools_excludes_rag_when_disabled():
    configure_claim_tool(object())
    tools = get_all_tools(include_rag_tool=False)
    names = _tool_names(tools)
    assert "get_rag_answer" not in names
    assert "answer_from_uploaded_document" not in names
    assert "submit_knowledge_report" in names
    assert "search_public_web" not in names
    assert "find_nearby_hospitals" in names
    assert "lookup_medical_facility" in names
    assert "request_location_quick_reply" in names
    assert "open_official_site" in names
    # verify_claim 與 get_rag_answer 同屬 include_rag_tool 這道開關，即使
    # claim 服務已配置，include_rag_tool=False 時仍不該出現。
    assert "verify_claim" not in names


def test_get_all_tools_excludes_claim_tool_when_not_configured():
    # 未呼叫 configure_claim_tool（或被設回 None）等同 dependencies.py 在
    # CLAIM_VERIFICATION_ENABLED=false 時「不建立服務也不 configure tool」
    # 之後的狀態，其餘 RAG 系工具不受影響。
    tools = get_all_tools(include_rag_tool=True)
    names = _tool_names(tools)
    assert "verify_claim" not in names
    assert "get_rag_answer" in names
